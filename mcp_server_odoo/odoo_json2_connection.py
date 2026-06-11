"""Odoo JSON/2 API connection management.

This module provides the OdooJSON2Connection class for connecting to
Odoo 19+ via the JSON/2 external API endpoint (/json/2/).

The JSON/2 API is Odoo 19's replacement for XML-RPC and JSON-RPC,
both of which are scheduled for removal in Odoo 20.

Reference: https://www.odoo.com/documentation/19.0/developer/reference/external_api.html
"""

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from curl_cffi import requests as cffi_requests
from curl_cffi.requests.errors import RequestsError

from .config import OdooConfig
from .error_sanitizer import ErrorSanitizer
from .exceptions import OdooConnectionError  # noqa: F401
from .odoo_json2_orm import Json2OrmMixin

logger = logging.getLogger(__name__)


class OdooJSON2Connection(Json2OrmMixin):
    """Manages connections to Odoo via the JSON/2 API.

    The JSON/2 API uses simple HTTP POST requests with Bearer token auth.
    Each request is a POST to /json/2/{model}/{method} with a flat JSON body.

    Key differences from XML-RPC:
    - Auth via Authorization: Bearer header (not uid+password per call)
    - Database via X-Odoo-Database header
    - All arguments are named (no positional args)
    - ids and context are top-level keys in request body
    - Responses are raw JSON (no RPC envelope)
    - Proper HTTP status codes for errors
    """

    DEFAULT_TIMEOUT = 30

    def __init__(self, config: OdooConfig, timeout: int = DEFAULT_TIMEOUT):
        """Initialize connection with configuration.

        Args:
            config: OdooConfig object with connection parameters
            timeout: HTTP request timeout in seconds
        """
        self.config = config
        self.timeout = timeout

        # Parse and validate URL
        parsed = urlparse(config.url)
        if parsed.scheme not in ("http", "https"):
            raise OdooConnectionError(f"Invalid URL scheme: {parsed.scheme}. Must be http or https")
        if not parsed.hostname:
            raise OdooConnectionError("Invalid URL: missing hostname")

        self._base_url = config.url.rstrip("/")
        self._json2_url = f"{self._base_url}/json/2"

        # Connection state
        self._connected = False
        self._authenticated = False
        self._uid: Optional[int] = None
        self._database: Optional[str] = None
        self._version: Optional[Dict[str, Any]] = None

        # HTTP client (created on connect). curl_cffi with browser-TLS
        # impersonation, so customer-side WAFs (Cloudflare bot-detection in
        # particular) don't reject us on TLS fingerprint.
        self._client: Optional[cffi_requests.Session] = None

        # Field cache
        self._fields_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}

        logger.info(f"Initialized OdooJSON2Connection for {parsed.hostname}")

    def _build_headers(self) -> Dict[str, str]:
        """Build HTTP headers for JSON/2 requests.

        Returns:
            Dict with Authorization, Content-Type, and X-Odoo-Database headers
        """
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "odoo-mcp-pro/1.0 (pnl-e5f1; pantalytics.com)",
        }
        if self._database:
            headers["X-Odoo-Database"] = self._database
        return headers

    def _call(self, model: str, method: str, **kwargs: Any) -> Any:
        """Make a JSON/2 API call.

        Args:
            model: Odoo model name (e.g., 'res.partner')
            method: ORM method name (e.g., 'search_read')
            **kwargs: Named arguments for the method. Special keys:
                - ids: list of record IDs (for record-level methods)
                - context: dict of context values

        Returns:
            The parsed JSON response (raw, no envelope)

        Raises:
            OdooConnectionError: If the request fails
        """
        if not self._client:
            raise OdooConnectionError("Not connected. Call connect() first.")

        url = f"{self._json2_url}/{model}/{method}"

        # Build request body from kwargs, filtering out None values
        body = {k: v for k, v in kwargs.items() if v is not None}

        logger.debug(f"JSON/2 call: POST {url} body={body}")

        try:
            response = self._client.post(url, json=body)
        except RequestsError as e:
            msg = str(e).lower()
            if "timeout" in msg or "timed out" in msg:
                raise OdooConnectionError(
                    f"Request timeout after {self.timeout}s: {model}/{method}"
                ) from None
            if "resolve" in msg or "connect" in msg:
                raise OdooConnectionError(f"Connection failed: {e}") from e
            raise OdooConnectionError(f"HTTP error: {e}") from e

        # Handle error responses
        if response.status_code == 200:
            return response.json()

        # Parse error body
        error_msg = self._parse_error_response(response)

        if response.status_code == 401:
            raise OdooConnectionError(f"Authentication failed: {error_msg}")
        elif response.status_code == 403:
            raise OdooConnectionError(f"Access denied: {error_msg}")
        elif response.status_code == 404:
            raise OdooConnectionError(f"Not found: {error_msg}")
        elif response.status_code == 422:
            raise OdooConnectionError(f"Invalid request: {error_msg}")
        else:
            raise OdooConnectionError(f"Server error ({response.status_code}): {error_msg}")

    def _parse_error_response(self, response: Any) -> str:
        """Extract error message from a JSON/2 error response.

        JSON/2 error responses contain:
        {
            "name": "exception.class.Name",
            "message": "human-readable message",
            "arguments": [...],
            "context": {},
            "debug": "full traceback"
        }
        """
        try:
            data = response.json()
            message = data.get("message", "")
            return ErrorSanitizer.sanitize_message(str(message))
        except Exception:
            return ErrorSanitizer.sanitize_message(response.text[:200])

    # --- Connection lifecycle ---

    def connect(self) -> None:
        """Establish connection to Odoo server.

        Creates a curl_cffi session with Chrome TLS impersonation and
        verifies the server is reachable by fetching the version endpoint.

        Raises:
            OdooConnectionError: If connection fails
        """
        if self._connected:
            logger.warning("Already connected to Odoo")
            return

        try:
            self._client = cffi_requests.Session(
                impersonate="chrome",
                timeout=self.timeout,
                allow_redirects=True,
            )

            # Test connection by fetching server version
            self._version = self._fetch_version()
            self._connected = True

            # /web/version returns {"version": ..., "version_info": [...]}
            # while xmlrpc /common.version() returns {"server_version": ...}.
            # Accept either to keep the log useful regardless of source.
            version_str = self._version.get("version") or self._version.get(
                "server_version", "unknown"
            )
            logger.info(f"Connected to Odoo {version_str}")

        except OdooConnectionError:
            self._cleanup_client()
            raise
        except Exception as e:
            self._cleanup_client()
            raise OdooConnectionError(f"Connection failed: {e}") from e

    def _fetch_version(self) -> Dict[str, Any]:
        """Fetch Odoo server version (no auth required).

        Returns:
            Version info dict

        Raises:
            OdooConnectionError: If request fails
        """
        try:
            response = self._client.get(f"{self._base_url}/web/version")
        except RequestsError as e:
            raise OdooConnectionError(f"Failed to fetch server version: {e}") from e

        if response.status_code != 200:
            raise OdooConnectionError(
                f"Failed to fetch server version: HTTP {response.status_code}"
            )
        try:
            return response.json()
        except Exception as e:
            raise OdooConnectionError(f"Failed to parse server version response: {e}") from e

    def disconnect(self) -> None:
        """Close connection and cleanup resources."""
        if not self._connected:
            return

        self._cleanup_client()
        self._connected = False
        self._authenticated = False
        self._uid = None
        self._database = None
        self._fields_cache.clear()

        logger.info("Disconnected from Odoo server")

    def _cleanup_client(self) -> None:
        """Close the HTTP client if open."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def authenticate(self, database: Optional[str] = None) -> None:
        """Authenticate with Odoo using Bearer token.

        For JSON/2, authentication is stateless — the API key is sent
        with every request. This method resolves the database name and
        retrieves the authenticated user's UID via context_get.

        Args:
            database: Database name. If not provided, uses config.database.

        Raises:
            OdooConnectionError: If authentication fails
        """
        if not self._connected:
            raise OdooConnectionError("Not connected. Call connect() first.")

        if not self.config.api_key:
            raise OdooConnectionError(
                "API key required for JSON/2 authentication. Set ODOO_API_KEY."
            )

        # Resolve database (optional for single-db instances like odoo.sh)
        self._database = database or self.config.database

        # Update client headers now that we have the database
        self._client.headers.update(self._build_headers())

        # Get UID by calling res.users/context_get
        try:
            context = self._call("res.users", "context_get")
            self._uid = context.get("uid")

            if not self._uid:
                raise OdooConnectionError("Authentication failed: could not retrieve user ID")

            self._authenticated = True
            logger.info(
                f"Authenticated via JSON/2 as UID {self._uid} on database '{self._database}'"
            )

        except OdooConnectionError:
            raise
        except Exception as e:
            raise OdooConnectionError(f"Authentication failed: {e}") from e

    # --- Properties ---

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    @property
    def uid(self) -> Optional[int]:
        return self._uid

    @property
    def database(self) -> Optional[str]:
        return self._database

    # --- Context manager ---

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def __del__(self):
        try:
            if hasattr(self, "_connected") and self._connected:
                self.disconnect()
        except (ValueError, AttributeError, RuntimeError):
            pass
