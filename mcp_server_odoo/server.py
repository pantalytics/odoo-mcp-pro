"""MCP Server implementation for Odoo.

This module provides the FastMCP server that exposes Odoo data
and functionality through the Model Context Protocol.
"""

import os
from typing import Any, Dict, Optional

from mcp.server import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .access_control import AccessController
from .config import OdooConfig, get_config
from .error_handling import (
    ConfigurationError,
    ErrorContext,
    error_handler,
)
from .logging_config import get_logger, logging_config, perf_logger
from .odoo_connection import OdooConnection, OdooConnectionError
from .odoo_json2_connection import OdooConnectionError as JSON2ConnectionError
from .odoo_json2_connection import OdooJSON2Connection
from .performance import PerformanceManager
from .resources import register_resources
from .tools import register_tools

# Set up logging
logger = get_logger(__name__)

# Server version
SERVER_VERSION = "0.5.0"


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

        # Configure OAuth if environment variables are set
        auth_settings, token_verifier = self._build_oauth_settings()

        # Create FastMCP instance with server metadata
        self.app = FastMCP(
            name="odoo-mcp-server",
            instructions="MCP server for accessing and managing Odoo ERP data through the Model Context Protocol",
            auth=auth_settings,
            token_verifier=token_verifier,
        )

        if auth_settings:
            logger.info(f"OAuth enabled (issuer: {auth_settings.issuer_url})")
            self._register_oauth_metadata_route(str(auth_settings.issuer_url))

        logger.info(f"Initialized Odoo MCP Server v{SERVER_VERSION}")

    def _register_oauth_metadata_route(self, issuer_url: str):
        """Register /.well-known/oauth-authorization-server endpoint.

        Claude.ai expects this endpoint on the MCP server to discover
        the authorization and token endpoints. We proxy to the external
        Zitadel OIDC configuration.
        """
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        @self.app.custom_route(
            "/.well-known/oauth-authorization-server",
            methods=["GET"],
        )
        async def oauth_metadata(request: Request) -> JSONResponse:
            issuer = issuer_url.rstrip("/")
            return JSONResponse({
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/oauth/v2/authorize",
                "token_endpoint": f"{issuer}/oauth/v2/token",
                "registration_endpoint": None,
                "scopes_supported": ["openid", "profile", "email"],
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["none"],
                "code_challenge_methods_supported": ["S256"],
            })

    @staticmethod
    def _build_oauth_settings():
        """Build OAuth auth settings from environment variables.

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
            raise ConfigurationError(
                f"OAUTH_ISSUER_URL is set but missing: {', '.join(missing)}"
            )

        from mcp.server.auth.settings import AuthSettings

        from .oauth import ZitadelTokenVerifier

        resource_server_url = os.getenv("OAUTH_RESOURCE_SERVER_URL", "").strip() or None

        auth_settings = AuthSettings(
            issuer_url=issuer_url,
            resource_server_url=resource_server_url,
        )

        token_verifier = ZitadelTokenVerifier(
            introspection_url=introspection_url,
            client_id=client_id,
            client_secret=client_secret,
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
                    if self.config.api_version == "json2":
                        # JSON/2 API (Odoo 19+)
                        logger.info("Using JSON/2 API for Odoo connection")
                        self.connection = OdooJSON2Connection(self.config)
                    else:
                        # XML-RPC (Odoo 14-19)
                        self.performance_manager = PerformanceManager(self.config)
                        self.connection = OdooConnection(
                            self.config, performance_manager=self.performance_manager
                        )

                    # Connect and authenticate
                    self.connection.connect()
                    self.connection.authenticate()

                logger.info(f"Successfully connected to Odoo at {self.config.url}")

                # Initialize access controller
                self.access_controller = AccessController(self.config)
            except Exception as e:
                context = ErrorContext(operation="connection_setup")
                # Let specific errors propagate as-is
                if isinstance(e, (OdooConnectionError, JSON2ConnectionError, ConfigurationError)):
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
        if not self.access_controller:
            self.access_controller = AccessController(self.config)
        self.resource_handler = register_resources(
            self.app, self.connection, self.access_controller, self.config
        )
        logger.info("Registered MCP resources")

    def _register_tools(self):
        """Register tool handlers after connection is established."""
        if not self.access_controller:
            self.access_controller = AccessController(self.config)
        self.tool_handler = register_tools(
            self.app, self.connection, self.access_controller, self.config
        )
        logger.info("Registered MCP tools")

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
        except (OdooConnectionError, JSON2ConnectionError, ConfigurationError):
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

        When OAuth env vars are configured, all requests require a valid
        Bearer token (validated via Zitadel introspection). The Odoo
        connection is always configured via server-side env vars.

        Args:
            host: Host to bind to
            port: Port to bind to
        """
        try:
            with perf_logger.track_operation("server_startup"):
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

            # Use FastMCP's built-in streamable HTTP (includes OAuth middleware if configured)
            await self.app.run_streamable_http_async()

        except KeyboardInterrupt:
            logger.info("Server interrupted by user")
        except (OdooConnectionError, JSON2ConnectionError, ConfigurationError):
            # Let these specific errors propagate
            raise
        except Exception as e:
            context = ErrorContext(operation="server_run_http")
            error_handler.handle_error(e, context=context)
        finally:
            # Always cleanup connection
            self._cleanup_connection()

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
        is_connected = (
            self.connection and self.connection.is_authenticated
            if hasattr(self.connection, "is_authenticated")
            else False
        )

        # Get performance stats if available
        performance_stats = None
        if self.performance_manager:
            performance_stats = self.performance_manager.get_stats()

        return {
            "status": "healthy" if is_connected else "unhealthy",
            "version": SERVER_VERSION,
            "connection": {
                "connected": is_connected,
                "url": self.config.url if self.config else None,
                "database": (
                    self.connection.database
                    if self.connection and hasattr(self.connection, "database")
                    else None
                ),
            },
            "error_metrics": error_handler.get_metrics(),
            "recent_errors": error_handler.get_recent_errors(limit=5),
            "performance": performance_stats,
        }
