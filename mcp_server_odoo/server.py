"""MCP Server implementation for Odoo.

This module provides the FastMCP server that exposes Odoo data
and functionality through the Model Context Protocol.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .registry import ConnectionRegistry

from mcp.server import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .access_control import AccessController
from .config import OdooConfig, get_config
from .error_handling import (
    ConfigurationError,
    ErrorContext,
    error_handler,
)
from .exceptions import OdooConnectionError
from .logging_config import get_logger, logging_config, perf_logger
from .odoo_connection import OdooConnection
from .odoo_json2_connection import OdooJSON2Connection
from .odoo_knowledge import SERVER_INSTRUCTIONS
from .performance import PerformanceManager
from .resources import register_resources
from .skills import register_skills
from .tools import register_tools
from .version_detect import detect_api_version

# Set up logging
logger = get_logger(__name__)

# Server version — keep in sync with pyproject.toml
SERVER_VERSION = "1.6.0"
GIT_COMMIT = os.environ.get("GIT_COMMIT", "unknown")
_BUILD_ORIGIN = "pnl-mcp-7f3a"  # Pantalytics provenance tag

# Dynamic Client Registration (RFC 7591) — allowed redirect_uri hosts.
# Intentionally not env-configurable: anti-abuse guardrail against arbitrary
# URIs being registered in Zitadel.
_DCR_ALLOWED_HOSTS = frozenset(
    {
        "chatgpt.com",
        "chat.openai.com",
        "claude.ai",
        "callback.mistral.ai",
        "localhost",
        "127.0.0.1",
    }
)
# Static-redirect hosts: each maps to a pre-configured OIDC app via
# environment variable. No Zitadel mutation happens for these — the
# redirect URIs are baked into the Zitadel app at setup time.
# Hosts NOT in this set go through the dynamic DCR app (chatgpt.com, etc.)
# whose redirectUris are appended at /register time.
_DCR_STATIC_HOSTS = frozenset(
    {
        "claude.ai",
        "callback.mistral.ai",
        "localhost",
        "127.0.0.1",
    }
)
_DCR_MAX_URIS_PER_REQUEST = 5


def create_fastmcp_app(*, auth=None, token_verifier=None) -> FastMCP:
    """Create the FastMCP app with the canonical server settings.

    Single source of truth for FastMCP construction — used by OdooMCPServer
    and by the private admin package's multi-tenant entry point. stateless_http
    so any replica can serve any request (blue/green deploys don't drop client
    connections with "No transport found for sessionId").
    """
    app = FastMCP(
        name="odoo-mcp-server",
        instructions=SERVER_INSTRUCTIONS,
        auth=auth,
        token_verifier=token_verifier,
        stateless_http=True,
        json_response=True,
    )
    # Skill resources — markdown workflow guides, no DB connection needed
    register_skills(app)
    return app


def _resolve_static_client_id(host: str) -> str:
    """Map a static-redirect host to its OIDC client_id from env.

    Each AI client gets its own Zitadel app for clean per-AI audit/revocation.
    Old env names (`MCP_OIDC_CLIENT_ID`, `MCP_OIDC_DCR_CLIENT_ID`) are
    accepted as fallbacks during the rename rollout — once prod has the
    new vars set, the fallback can be dropped in a follow-up.

    Returns "" if the host isn't static or no env is configured.
    """
    if host in {"claude.ai", "localhost", "127.0.0.1"}:
        return (
            os.getenv("MCP_CLAUDE_CLIENT_ID", "").strip()
            or os.getenv("MCP_OIDC_CLIENT_ID", "").strip()
        )
    if host == "callback.mistral.ai":
        return os.getenv("MCP_LECHAT_CLIENT_ID", "").strip()
    return ""


def _resolve_static_client_secret(host: str) -> str:
    """Map a static-redirect host to its OIDC client_secret, if any.

    Most AI clients (Claude, ChatGPT) are public clients and use PKCE only.
    Le Chat is a confidential client per Mistral's flow: it sends
    `token_endpoint_auth_method: "client_secret_basic"` during DCR and
    expects a `client_secret` in the /register response, which it then
    uses to authenticate at the Zitadel /token endpoint (alongside PKCE).

    Returns "" for hosts that should be public clients.
    """
    if host == "callback.mistral.ai":
        return os.getenv("MCP_LECHAT_CLIENT_SECRET", "").strip()
    return ""


def _resolve_dcr_env() -> dict:
    """Return the DCR (dynamic-host) Zitadel env config.

    Accepts both `MCP_CHATGPT_*` (new) and `MCP_OIDC_DCR_*` (old) names,
    new wins. Empty values mean the dynamic-path is not configured.
    """

    def _pick(new: str, old: str) -> str:
        return os.getenv(new, "").strip() or os.getenv(old, "").strip()

    return {
        "client_id": _pick("MCP_CHATGPT_CLIENT_ID", "MCP_OIDC_DCR_CLIENT_ID"),
        "app_id": _pick("MCP_CHATGPT_APP_ID", "MCP_OIDC_DCR_APP_ID"),
        "project_id": _pick("MCP_CHATGPT_PROJECT_ID", "MCP_OIDC_DCR_PROJECT_ID"),
    }


class _DCRUpdateError(Exception):
    """Raised when mutating the DCR app in Zitadel fails."""


async def _append_redirect_uris_to_dcr_app(
    *,
    zitadel_base_url: str,
    pat: str,
    project_id: str,
    app_id: str,
    new_uris: List[str],
) -> None:
    """Merge new_uris into the DCR app's redirectUris via Zitadel mgmt API.

    Zitadel's UpdateOIDCAppConfig is a full-replace PUT, so we GET the
    current oidcConfig, merge, and PUT the whole thing back.
    """
    import httpx

    base = zitadel_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json",
    }
    get_url = f"{base}/management/v1/projects/{project_id}/apps/{app_id}"
    put_url = f"{get_url}/oidc_config"

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(get_url, headers=headers)
        except httpx.HTTPError as e:
            raise _DCRUpdateError(f"GET app network error: {e}") from e
        if r.status_code != 200:
            raise _DCRUpdateError(f"GET app failed: {r.status_code} {r.text[:200]}")
        oidc = (r.json().get("app") or {}).get("oidcConfig") or {}
        if not oidc:
            raise _DCRUpdateError("app has no oidcConfig (wrong app_id?)")

        existing = set(oidc.get("redirectUris") or [])
        merged = existing | set(new_uris)
        if merged == existing:
            # All URIs already registered — nothing to do.
            return

        # PUT requires the full OIDC config. Preserve every field we got
        # from GET; only override redirectUris.
        put_body = {
            k: v
            for k, v in {
                "redirectUris": sorted(merged),
                "responseTypes": oidc.get("responseTypes"),
                "grantTypes": oidc.get("grantTypes"),
                "appType": oidc.get("appType"),
                "authMethodType": oidc.get("authMethodType"),
                "postLogoutRedirectUris": oidc.get("postLogoutRedirectUris"),
                "devMode": oidc.get("devMode"),
                "accessTokenType": oidc.get("accessTokenType"),
                "accessTokenRoleAssertion": oidc.get("accessTokenRoleAssertion"),
                "idTokenRoleAssertion": oidc.get("idTokenRoleAssertion"),
                "idTokenUserinfoAssertion": oidc.get("idTokenUserinfoAssertion"),
                "clockSkew": oidc.get("clockSkew"),
                "additionalOrigins": oidc.get("additionalOrigins"),
                "skipNativeAppSuccessPage": oidc.get("skipNativeAppSuccessPage"),
                "backChannelLogoutUri": oidc.get("backChannelLogoutUri"),
                "loginVersion": oidc.get("loginVersion"),
            }.items()
            if v is not None
        }

        try:
            r = await client.put(put_url, headers=headers, json=put_body)
        except httpx.HTTPError as e:
            raise _DCRUpdateError(f"PUT config network error: {e}") from e
        if r.status_code == 200:
            return
        # Zitadel returns 400 "No changes" (COMMAND-1m88i) when the PUT
        # body matches current state. Happens on GET-PUT races with a
        # concurrent /register call that already added our URI. Idempotent
        # outcome either way — treat as success.
        if r.status_code == 400 and "No changes" in r.text:
            return
        raise _DCRUpdateError(f"PUT config failed: {r.status_code} {r.text[:200]}")


class OdooMCPServer:
    """Main MCP server class for Odoo integration.

    This class manages the FastMCP server instance and maintains
    the connection to Odoo. The server lifecycle is managed by
    establishing connection before starting and cleaning up on exit.
    """

    def __init__(self, config: Optional[OdooConfig] = None):
        """Initialize the Odoo MCP server.

        Args:
            config: Optional OdooConfig instance. If not provided,
                   will load from environment variables.
        """
        # Load configuration
        self.config = config or get_config()

        # Set up structured logging
        logging_config.setup()

        # Initialize connection and access controller (will be created on startup)
        self.connection = None  # OdooConnection or OdooJSON2Connection
        self.access_controller: Optional[AccessController] = None
        self.performance_manager: Optional[PerformanceManager] = None
        self.resource_handler = None
        self.tool_handler = None

        # Multi-tenant registry (HTTP mode with admin panel).
        # DatabaseManager type is intentionally Any: it lives in the
        # private admin package, which isn't a static dependency of the
        # public repo. Lazy-imported at runtime in _setup_multi_tenant.
        self.db_manager: Optional[Any] = None
        self.registry: Optional[ConnectionRegistry] = None

        # Configure OAuth if environment variables are set
        auth_settings, token_verifier = self._build_oauth_settings()

        self.app = create_fastmcp_app(auth=auth_settings, token_verifier=token_verifier)

        if auth_settings:
            logger.info(f"OAuth enabled (issuer: {auth_settings.issuer_url})")
            resource_url = (
                str(auth_settings.resource_server_url)
                if auth_settings.resource_server_url
                else None
            )
            oauth_issuer_url = os.getenv("OAUTH_ISSUER_URL", "").strip()
            self._register_oauth_metadata_route(
                str(auth_settings.issuer_url),
                resource_server_url=resource_url,
                zitadel_issuer_url=oauth_issuer_url,
            )

        # Skill resources — markdown workflow guides, no DB connection needed
        register_skills(self.app)

        logger.info(f"Initialized Odoo MCP Server v{SERVER_VERSION}")

    def _register_oauth_metadata_route(
        self,
        issuer_url: str,
        resource_server_url: str | None = None,
        zitadel_issuer_url: str = "",
    ):
        """Register OAuth metadata endpoints.

        Registers:
        - /.well-known/oauth-protected-resource (RFC 9728 PRM)
        - /.well-known/oauth-authorization-server (RFC 8414 OASM)
        - /register (RFC 7591 DCR — returns pre-configured client_id)

        Claude.ai (web) constructs auth URLs relative to the MCP server root,
        so we serve our own OASM that points to Zitadel's actual endpoints.
        The DCR endpoint allows Claude.ai to auto-discover the client_id
        without users needing to enter it in Advanced Settings.
        """
        if not resource_server_url:
            return

        from starlette.requests import Request
        from starlette.responses import JSONResponse

        zitadel = zitadel_issuer_url.rstrip("/") if zitadel_issuer_url else issuer_url.rstrip("/")
        # Extract server root (without /mcp path) for authorization_servers

        parsed = urlparse(resource_server_url)
        server_root = f"{parsed.scheme}://{parsed.netloc}"

        @self.app.custom_route(
            "/.well-known/oauth-protected-resource",
            methods=["GET"],
        )
        async def protected_resource_metadata(request: Request) -> JSONResponse:
            return JSONResponse(
                {
                    "resource": resource_server_url,
                    "authorization_servers": [server_root],
                    "scopes_supported": [
                        "openid",
                        "profile",
                        "email",
                        "offline_access",
                    ],
                    "bearer_methods_supported": ["header"],
                }
            )

        @self.app.custom_route(
            "/.well-known/oauth-authorization-server",
            methods=["GET"],
        )
        async def oauth_authorization_server_metadata(request: Request) -> JSONResponse:
            return JSONResponse(
                {
                    "issuer": server_root,
                    "authorization_endpoint": f"{zitadel}/oauth/v2/authorize",
                    "token_endpoint": f"{zitadel}/oauth/v2/token",
                    "registration_endpoint": f"{server_root}/register",
                    "scopes_supported": [
                        "openid",
                        "profile",
                        "email",
                        "offline_access",
                    ],
                    "response_types_supported": ["code"],
                    "grant_types_supported": [
                        "authorization_code",
                        "refresh_token",
                    ],
                    "token_endpoint_auth_methods_supported": [
                        "none",
                        "client_secret_basic",
                        "client_secret_post",
                    ],
                    "code_challenge_methods_supported": ["S256"],
                }
            )

        @self.app.custom_route("/register", methods=["POST"])
        async def register_client(request: Request) -> JSONResponse:
            """RFC 7591 Dynamic Client Registration.

            Per-AI router based on redirect_uri host:

            - claude.ai / localhost → MCP_CLAUDE_CLIENT_ID (static app)
            - callback.mistral.ai → MCP_LECHAT_CLIENT_ID (static app)
            - chatgpt.com / chat.openai.com → MCP_CHATGPT_CLIENT_ID
              (dynamic DCR app: redirect URIs are appended to its
              allowlist via the Zitadel management API at registration time)

            Host allowlist is intentionally hardcoded. Any URI outside
            the allowlist → 400.
            """
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {
                        "error": "invalid_client_metadata",
                        "error_description": "body must be JSON",
                    },
                    status_code=400,
                )

            raw_uris = body.get("redirect_uris") or []
            if not isinstance(raw_uris, list) or not raw_uris:
                return JSONResponse(
                    {
                        "error": "invalid_redirect_uri",
                        "error_description": "redirect_uris required",
                    },
                    status_code=400,
                )
            if len(raw_uris) > _DCR_MAX_URIS_PER_REQUEST:
                return JSONResponse(
                    {
                        "error": "invalid_redirect_uri",
                        "error_description": (
                            f"at most {_DCR_MAX_URIS_PER_REQUEST} URIs per request"
                        ),
                    },
                    status_code=400,
                )

            hosts: list[str] = []
            for uri in raw_uris:
                if not isinstance(uri, str):
                    return JSONResponse(
                        {
                            "error": "invalid_redirect_uri",
                            "error_description": "uri must be a string",
                        },
                        status_code=400,
                    )
                try:
                    parsed = urlparse(uri)
                except Exception:
                    return JSONResponse(
                        {
                            "error": "invalid_redirect_uri",
                            "error_description": f"invalid uri: {uri}",
                        },
                        status_code=400,
                    )
                host = (parsed.hostname or "").lower()
                if host not in _DCR_ALLOWED_HOSTS:
                    logger.warning(f"DCR rejected: host={host!r} not in allowlist (uri={uri})")
                    return JSONResponse(
                        {
                            "error": "invalid_redirect_uri",
                            "error_description": f"host {host!r} not allowed",
                        },
                        status_code=400,
                    )
                hosts.append(host)

            client_name = body.get("client_name", "unknown")
            all_static = all(h in _DCR_STATIC_HOSTS for h in hosts)

            common_response_fields = {
                "client_name": client_name,
                "redirect_uris": raw_uris,
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            }

            if all_static:
                # Each static host maps to its own AI-specific Zitadel app.
                # Reject mixed-AI requests: a single /register call cannot
                # return one client_id for two different apps.
                client_ids = {_resolve_static_client_id(h) for h in hosts}
                client_ids.discard("")
                if len(client_ids) > 1:
                    logger.warning(
                        f"DCR rejected: mixed AI hosts in one request: {sorted(set(hosts))}"
                    )
                    return JSONResponse(
                        {
                            "error": "invalid_redirect_uri",
                            "error_description": (
                                "redirect_uris span multiple AI clients; register one AI at a time"
                            ),
                        },
                        status_code=400,
                    )
                client_id = client_ids.pop() if client_ids else ""
                if not client_id:
                    # Resolved to "" — env var for this AI is unset on the
                    # server. Loud error so we notice in logs/telemetry.
                    logger.error(
                        f"DCR static-path: no client_id configured for hosts={sorted(set(hosts))}"
                    )
                    return JSONResponse(
                        {
                            "error": "server_error",
                            "error_description": (
                                "no client_id configured for this AI on the server"
                            ),
                        },
                        status_code=500,
                    )
                # Confidential clients (e.g. Le Chat) need the client_secret
                # echoed back in DCR so they can authenticate at /token via
                # client_secret_basic. Public clients (Claude, localhost)
                # use PKCE only. all_static guarantees all hosts map to the
                # same AI app, so picking hosts[0] is safe.
                client_secret = _resolve_static_client_secret(hosts[0])
                if client_secret:
                    static_response = {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "token_endpoint_auth_method": "client_secret_basic",
                        **common_response_fields,
                    }
                else:
                    static_response = {
                        "client_id": client_id,
                        "token_endpoint_auth_method": "none",
                        **common_response_fields,
                    }
                logger.info(
                    f"DCR static-path: client={client_name!r} "
                    f"hosts={sorted(set(hosts))} "
                    f"auth={static_response['token_endpoint_auth_method']}"
                )
                return JSONResponse(static_response)

            # Dynamic-path — mutate DCR app in Zitadel
            dcr = _resolve_dcr_env()
            dcr_client_id = dcr["client_id"]
            dcr_app_id = dcr["app_id"]
            dcr_project_id = dcr["project_id"]
            zitadel_pat = os.getenv("ZITADEL_PAT", "").strip()

            missing = [
                name
                for name, value in [
                    ("MCP_CHATGPT_CLIENT_ID", dcr_client_id),
                    ("MCP_CHATGPT_APP_ID", dcr_app_id),
                    ("MCP_CHATGPT_PROJECT_ID", dcr_project_id),
                    ("ZITADEL_PAT", zitadel_pat),
                ]
                if not value
            ]
            if missing:
                logger.error(f"DCR dynamic-path blocked: missing env: {missing}")
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": (
                            f"DCR not configured on server (missing: {', '.join(missing)})"
                        ),
                    },
                    status_code=500,
                )

            try:
                await _append_redirect_uris_to_dcr_app(
                    zitadel_base_url=zitadel,
                    pat=zitadel_pat,
                    project_id=dcr_project_id,
                    app_id=dcr_app_id,
                    new_uris=raw_uris,
                )
            except _DCRUpdateError as e:
                logger.error(f"DCR dynamic-path failed: {e}")
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": f"Failed to update DCR app: {e}",
                    },
                    status_code=500,
                )

            logger.info(
                f"DCR dynamic-path: client={client_name!r} "
                f"hosts={sorted(set(hosts))} uris={len(raw_uris)}"
            )
            return JSONResponse(
                {
                    "client_id": dcr_client_id,
                    "token_endpoint_auth_method": "none",
                    **common_response_fields,
                }
            )

    @staticmethod
    def _build_oauth_settings():
        """Build OAuth auth settings from environment variables.

        Security design:
        - Claude.ai is a public client: uses PKCE (S256) instead of a
          client_secret. This is correct per OAuth 2.1 — browser/CLI
          clients cannot securely store secrets.
        - The MCP server validates tokens via Zitadel introspection using
          its own client_id:client_secret (confidential, server-side only).
        - Audience validation ensures tokens were issued for this server.
        - Required scopes are enforced at both introspection and middleware.

        Returns:
            Tuple of (AuthSettings | None, TokenVerifier | None).
            Both are None if OAuth is not configured.
        """
        issuer_url = os.getenv("OAUTH_ISSUER_URL", "").strip()
        introspection_url = os.getenv("ZITADEL_INTROSPECTION_URL", "").strip()
        client_id = os.getenv("ZITADEL_CLIENT_ID", "").strip()
        client_secret = os.getenv("ZITADEL_CLIENT_SECRET", "").strip()

        if not issuer_url:
            return None, None

        # Validate that all required OAuth vars are present
        missing = []
        if not introspection_url:
            missing.append("ZITADEL_INTROSPECTION_URL")
        if not client_id:
            missing.append("ZITADEL_CLIENT_ID")
        if not client_secret:
            missing.append("ZITADEL_CLIENT_SECRET")
        if missing:
            raise ConfigurationError(f"OAUTH_ISSUER_URL is set but missing: {', '.join(missing)}")

        from mcp.server.auth.settings import AuthSettings

        from .oauth import ZitadelTokenVerifier

        resource_server_url = os.getenv("OAUTH_RESOURCE_SERVER_URL", "").strip() or None

        # Required scopes that every token must have (enforced by MCP middleware)
        required_scopes = ["openid"]

        # Use the server root (without /mcp path) as issuer URL.
        # The MCP SDK uses this in the auto-generated PRM's authorization_servers.
        # Claude.ai discovers OASM at this URL, where we serve our custom metadata
        # that proxies auth endpoints to Zitadel.
        if resource_server_url:
            from urllib.parse import urlparse as _urlparse

            _parsed = _urlparse(resource_server_url)
            oasm_issuer = f"{_parsed.scheme}://{_parsed.netloc}"
        else:
            oasm_issuer = issuer_url
        auth_settings = AuthSettings(
            issuer_url=oasm_issuer,
            resource_server_url=resource_server_url,
            required_scopes=required_scopes,
        )

        # Audience validation: Zitadel uses project/app IDs as audience,
        # not the resource server URL. Use OAUTH_EXPECTED_AUDIENCE if set,
        # otherwise skip audience validation (Zitadel introspection already
        # confirms the token is valid for this project).
        expected_audience = os.getenv("OAUTH_EXPECTED_AUDIENCE", "").strip() or None

        token_verifier = ZitadelTokenVerifier(
            introspection_url=introspection_url,
            client_id=client_id,
            client_secret=client_secret,
            expected_audience=expected_audience,
            required_scopes=required_scopes,
        )

        return auth_settings, token_verifier

    def _ensure_connection(self):
        """Ensure connection to Odoo is established.

        Raises:
            ConnectionError: If connection fails
            ConfigurationError: If configuration is invalid
        """
        if not self.connection:
            try:
                logger.info("Establishing connection to Odoo...")
                with perf_logger.track_operation("connection_setup"):
                    # Auto-detect API version from Odoo server version
                    api_version, server_version = detect_api_version(self.config.url)
                    if api_version == "unknown":
                        # Both XML-RPC and /web/version probes failed. In OSS
                        # standalone usage we have no UI to ask the user, so
                        # fall back to xmlrpc and let authenticate() raise with
                        # the real reason.
                        logger.warning(
                            "Could not auto-detect Odoo version; falling back to xmlrpc. "
                            "If you are on Odoo 19+, set ODOO_API_VERSION=json2 explicitly."
                        )
                        api_version = "xmlrpc"
                    self.config.api_version = api_version
                    logger.info(
                        f"Auto-detected api_version={api_version}"
                        f" (Odoo {server_version or 'unknown'})"
                    )

                    if api_version == "json2":
                        # JSON/2 API (Odoo 19+)
                        logger.info("Using JSON/2 API for Odoo connection")
                        self.connection = OdooJSON2Connection(self.config)
                    else:
                        # XML-RPC (Odoo 14-18)
                        self.performance_manager = PerformanceManager(self.config)
                        self.connection = OdooConnection(
                            self.config, performance_manager=self.performance_manager
                        )

                    # Connect and authenticate
                    self.connection.connect()
                    self.connection.authenticate()

                logger.info(f"Successfully connected to Odoo at {self.config.url}")

                # Initialize access controller — pass connection for JSON/2 permission fetching
                self.access_controller = AccessController(self.config, connection=self.connection)
            except Exception as e:
                context = ErrorContext(operation="connection_setup")
                # Let specific errors propagate as-is
                if isinstance(e, (OdooConnectionError, ConfigurationError)):
                    raise
                # Handle other unexpected errors
                error_handler.handle_error(e, context=context)

    def _cleanup_connection(self):
        """Clean up Odoo connection."""
        if self.connection:
            try:
                logger.info("Closing Odoo connection...")
                self.connection.disconnect()
            except Exception as e:
                logger.error(f"Error closing connection: {e}")
            finally:
                # Always clear connection reference
                self.connection = None
                self.access_controller = None
                self.resource_handler = None
                self.tool_handler = None

    def _register_resources(self):
        """Register resource handlers after connection is established."""
        self.resource_handler = register_resources(
            self.app, self.connection, self.access_controller, self.config
        )
        logger.info("Registered MCP resources")

    def _register_tools(self):
        """Register tool handlers after connection is established."""
        self.tool_handler = register_tools(
            self.app, self.connection, self.access_controller, self.config
        )
        logger.info("Registered MCP tools")

    def _register_resources_with_registry(self):
        """Register resource handlers with ConnectionRegistry (multi-tenant)."""
        self.resource_handler = register_resources(
            self.app, config=self.config, registry=self.registry
        )
        logger.info("Registered MCP resources (multi-tenant)")

    def _register_tools_with_registry(self):
        """Register tool handlers with ConnectionRegistry (multi-tenant)."""
        self.tool_handler = register_tools(
            self.app,
            config=self.config,
            registry=self.registry,
            usage_tracker=getattr(self, "usage_tracker", None),
        )
        logger.info("Registered MCP tools (multi-tenant)")

    async def run_stdio(self):
        """Run the server using stdio transport.

        This is the main entry point for running the server
        with standard input/output transport (used by uvx).
        """
        try:
            # Establish connection before starting server
            with perf_logger.track_operation("server_startup"):
                self._ensure_connection()

                # Register resources after connection is established
                self._register_resources()
                self._register_tools()

            logger.info("Starting MCP server with stdio transport...")
            await self.app.run_stdio_async()

        except KeyboardInterrupt:
            logger.info("Server interrupted by user")
        except (OdooConnectionError, ConfigurationError):
            # Let these specific errors propagate
            raise
        except Exception as e:
            context = ErrorContext(operation="server_run")
            error_handler.handle_error(e, context=context)
        finally:
            # Always cleanup connection
            self._cleanup_connection()

    def run_stdio_sync(self):
        """Synchronous wrapper for run_stdio.

        This is provided for compatibility with synchronous code.
        """
        import asyncio

        asyncio.run(self.run_stdio())

    # SSE transport has been deprecated in MCP protocol version 2025-03-26
    # Use streamable-http transport instead

    async def run_http(self, host: str = "localhost", port: int = 8000):
        """Run the server using streamable HTTP transport.

        Two modes:
        - Single-tenant: Uses a single Odoo connection (env vars)
        - Multi-tenant: Uses ConnectionRegistry + DatabaseManager (DATABASE_URL set)

        When OAuth env vars are configured, all requests require a valid
        Bearer token (validated via Zitadel introspection).

        Args:
            host: Host to bind to
            port: Port to bind to
        """
        try:
            database_url = os.getenv("DATABASE_URL", "").strip()

            with perf_logger.track_operation("server_startup"):
                if database_url:
                    # Multi-tenant mode: requires odoo-mcp-pro-admin package
                    try:
                        from .admin.db import DatabaseManager
                    except ImportError as e:
                        raise ConfigurationError(
                            "DATABASE_URL is set but the admin package is not installed. "
                            "Install odoo-mcp-pro-admin for multi-tenant mode, or unset "
                            "DATABASE_URL for single-tenant mode."
                        ) from e
                    from .registry import ConnectionRegistry

                    logger.info("Starting in multi-tenant mode (DATABASE_URL configured)")
                    self.db_manager = DatabaseManager(database_url)
                    await self.db_manager.connect()
                    self.registry = ConnectionRegistry(self.db_manager)

                    # Initialize usage tracking
                    from .usage import UsageTracker

                    self.usage_tracker = UsageTracker(self.db_manager._pool)
                    logger.info("Usage tracking enabled")

                    # Register tools/resources with registry (no single connection)
                    self._register_resources_with_registry()
                    self._register_tools_with_registry()
                else:
                    # Single-tenant mode: single connection from env vars
                    self._ensure_connection()
                    self._register_resources()
                    self._register_tools()

            logger.info(f"Starting MCP server with HTTP transport on {host}:{port}...")

            # Update FastMCP settings for host and port
            self.app.settings.host = host
            self.app.settings.port = port

            # Disable DNS rebinding protection when binding to all interfaces
            if host == "0.0.0.0":
                self.app.settings.transport_security = TransportSecuritySettings(
                    enable_dns_rebinding_protection=False
                )

            # Build ASGI app: MCP + optional admin panel
            asgi_app = self.app.streamable_http_app()

            if database_url:
                # Multi-tenant: mount admin panel alongside MCP (requires admin package)
                try:
                    from starlette.routing import Mount

                    from .admin.app import create_admin_app

                    issuer_url = os.getenv("OAUTH_ISSUER_URL", "").strip()
                    admin_app = create_admin_app(
                        db_manager=self.db_manager,
                        registry=self.registry,
                        zitadel_issuer_url=issuer_url,
                        usage_tracker=self.usage_tracker,
                    )
                    asgi_app.routes.insert(0, Mount("/admin", app=admin_app, name="admin"))
                except ImportError:
                    logger.warning("Admin panel not available (odoo-mcp-pro-admin not installed)")
                logger.info("Admin panel mounted at /admin")

            from .usage import track_event

            track_event(
                "mcp_server_started",
                properties={
                    "git_commit": os.getenv("GIT_COMMIT", "unknown"),
                    "multi_tenant": bool(database_url),
                },
            )

            import uvicorn

            config = uvicorn.Config(
                asgi_app,
                host=host,
                port=port,
                log_level=self.app.settings.log_level.lower(),
            )
            server = uvicorn.Server(config)
            await server.serve()

        except KeyboardInterrupt:
            logger.info("Server interrupted by user")
        except (OdooConnectionError, ConfigurationError):
            # Let these specific errors propagate
            raise
        except Exception as e:
            context = ErrorContext(operation="server_run_http")
            error_handler.handle_error(e, context=context)
        finally:
            # Cleanup
            self._cleanup_connection()
            if self.registry:
                self.registry.close_all()
                self.registry = None
            if self.db_manager:
                await self.db_manager.close()
                self.db_manager = None

    def get_capabilities(self) -> Dict[str, Dict[str, bool]]:
        """Get server capabilities.

        Returns:
            Dict with server capabilities
        """
        return {
            "capabilities": {
                "resources": True,  # Exposes Odoo data as resources
                "tools": True,  # Provides tools for Odoo operations
                "prompts": False,  # Prompts will be added in later phases
            }
        }

    def get_health_status(self) -> Dict[str, Any]:
        """Get server health status with error metrics.

        Returns:
            Dict with health status and metrics
        """
        is_connected = bool(self.connection and self.connection.is_authenticated)

        # Get performance stats if available
        performance_stats = None
        if self.performance_manager:
            performance_stats = self.performance_manager.get_stats()

        return {
            "status": "healthy" if is_connected else "unhealthy",
            "version": SERVER_VERSION,
            "git_commit": GIT_COMMIT,
            "connection": {
                "connected": is_connected,
                "url": self.config.url if self.config else None,
                "database": self.connection.database if self.connection else None,
            },
            "error_metrics": error_handler.get_metrics(),
            "recent_errors": error_handler.get_recent_errors(limit=5),
            "performance": performance_stats,
        }
