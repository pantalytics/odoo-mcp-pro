"""MCP Server implementation for Odoo.

This module provides the FastMCP server that exposes Odoo data
and functionality through the Model Context Protocol.
"""

from __future__ import annotations

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
SERVER_VERSION = "1.8.0"
GIT_COMMIT = os.environ.get("GIT_COMMIT", "unknown")
_BUILD_ORIGIN = "pnl-mcp-7f3a"  # Pantalytics provenance tag


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

        self.app = create_fastmcp_app()

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

        Single-tenant: one Odoo connection from env vars. The transport
        itself is unauthenticated — put it behind your own reverse-proxy
        auth when exposing it beyond localhost. (The hosted multi-tenant
        deployment lives in the private odoo-mcp-pro-admin package, which
        has its own entry point.)

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

            asgi_app = self.app.streamable_http_app()

            from .usage import track_event

            track_event(
                "mcp_server_started",
                properties={
                    "git_commit": os.getenv("GIT_COMMIT", "unknown"),
                    "multi_tenant": False,
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
