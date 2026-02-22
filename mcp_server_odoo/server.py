"""MCP Server implementation for Odoo.

This module provides the FastMCP server that exposes Odoo data
and functionality through the Model Context Protocol.
"""

import threading
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

from mcp.server import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from .access_control import AccessController
from .config import OdooConfig, get_config
from .connection_context import current_connection
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
SERVER_VERSION = "0.4.0"

class ConnectionPool:
    """Cache Odoo connections by (url, api_key) tuple."""

    def __init__(self):
        self._connections: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()

    def get_or_create(self, odoo_url: str, api_key: str) -> Any:
        key = (odoo_url, api_key)
        with self._lock:
            if key not in self._connections:
                logger.info(f"Creating new connection for {odoo_url}")
                config = OdooConfig(url=odoo_url, api_key=api_key, api_version="json2")
                conn = OdooJSON2Connection(config)
                conn.connect()
                conn.authenticate()
                self._connections[key] = conn
                logger.info(f"Connected to {odoo_url}")
            return self._connections[key]


class OdooConnectionMiddleware:
    """ASGI middleware that sets per-request Odoo connection from query params.

    Reads odoo_url and api_key from URL query parameters and sets the
    current_connection context variable for the duration of the request.
    Falls back to the default connection if no query params are provided.
    """

    def __init__(self, app: ASGIApp, pool: ConnectionPool, default_connection: Any = None):
        self.app = app
        self.pool = pool
        self.default_connection = default_connection

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            query_string = scope.get("query_string", b"").decode()
            params = parse_qs(query_string)

            odoo_url = params.get("odoo_url", [None])[0]
            api_key = params.get("api_key", [None])[0]

            conn = self.default_connection
            if odoo_url and api_key:
                try:
                    conn = self.pool.get_or_create(odoo_url, api_key)
                except Exception as e:
                    logger.error(f"Failed to connect to {odoo_url}: {e}")

            if conn:
                token = current_connection.set(conn)
                try:
                    await self.app(scope, receive, send)
                finally:
                    current_connection.reset(token)
                return

        await self.app(scope, receive, send)


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

        # Create FastMCP instance with server metadata
        self.app = FastMCP(
            name="odoo-mcp-server",
            instructions="MCP server for accessing and managing Odoo ERP data through the Model Context Protocol",
        )

        logger.info(f"Initialized Odoo MCP Server v{SERVER_VERSION}")

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
        # Pass connection (may be None in multi-tenant mode; handlers use context var)
        self.resource_handler = register_resources(
            self.app, self.connection, self.access_controller, self.config
        )
        logger.info("Registered MCP resources")

    def _register_tools(self):
        """Register tool handlers after connection is established."""
        if not self.access_controller:
            self.access_controller = AccessController(self.config)
        # Pass connection (may be None in multi-tenant mode; handlers use context var)
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

        Supports multi-tenant mode: if odoo_url and api_key are provided as
        URL query parameters, a per-request connection is created. Otherwise
        falls back to the env-var-configured connection.

        Args:
            host: Host to bind to
            port: Port to bind to
        """
        try:
            # Try to establish default connection from env vars (optional in multi-tenant mode)
            try:
                with perf_logger.track_operation("server_startup"):
                    self._ensure_connection()
            except Exception as e:
                logger.warning(
                    f"No default connection configured: {e}. "
                    "Running in multi-tenant mode (credentials via query params only)."
                )

            # Register tools/resources (they'll use current_connection context var)
            with perf_logger.track_operation("server_startup"):
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

            # Create connection pool and wrap ASGI app with middleware
            self._connection_pool = ConnectionPool()
            starlette_app = self.app.streamable_http_app()
            wrapped_app = OdooConnectionMiddleware(
                starlette_app, self._connection_pool, self.connection
            )

            # Run with uvicorn (same as FastMCP.run_streamable_http_async but with middleware)
            import uvicorn

            config = uvicorn.Config(
                wrapped_app,
                host=host,
                port=port,
                log_level=self.app.settings.log_level.lower(),
            )
            server = uvicorn.Server(config)
            await server.serve()

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
