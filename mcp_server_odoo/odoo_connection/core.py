# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""Core OdooConnection class and connection lifecycle management.

This module provides the OdooConnection class for managing connections
to Odoo via XML-RPC using MCP-specific endpoints. Authentication and
ORM methods are provided by mixins in `auth.py` and `orm.py`.
"""

import logging
import socket
import xmlrpc.client
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from ..config import OdooConfig
from ..exceptions import OdooConnectionError
from ..performance import PerformanceManager
from .auth import OdooConnectionAuthMixin
from .orm import OdooConnectionOrmMixin

logger = logging.getLogger(__name__)


class OdooConnection(OdooConnectionAuthMixin, OdooConnectionOrmMixin):
    """Manages XML-RPC connections to Odoo with dynamic endpoint selection.

    This class provides connection management, health checking, and
    proper resource cleanup for Odoo XML-RPC connections. Uses standard
    XML-RPC endpoints with API key authentication.
    """

    # Connection timeout in seconds
    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        config: OdooConfig,
        timeout: int = DEFAULT_TIMEOUT,
        performance_manager: Optional[PerformanceManager] = None,
    ):
        """Initialize connection with configuration.

        Args:
            config: OdooConfig object with connection parameters
            timeout: Connection timeout in seconds
            performance_manager: Optional performance manager for optimizations
        """
        self.config = config
        self.timeout = timeout
        self._url_components = self._parse_url(config.url)
        # Mirror OdooJSON2Connection._base_url so server_info() and any other
        # caller that wants to know "which Odoo am I connected to" doesn't
        # have to special-case the two connection types.
        self._base_url = (config.url or "").rstrip("/")

        # Get appropriate endpoints based on mode
        endpoints = config.get_endpoint_paths()
        self.DB_ENDPOINT = endpoints["db"]
        self.COMMON_ENDPOINT = endpoints["common"]
        self.OBJECT_ENDPOINT = endpoints["object"]

        # Performance manager for optimizations
        self._performance_manager = performance_manager or PerformanceManager(config)

        # XML-RPC proxies (created on connect)
        self._db_proxy: Optional[xmlrpc.client.ServerProxy] = None
        self._common_proxy: Optional[xmlrpc.client.ServerProxy] = None
        self._object_proxy: Optional[xmlrpc.client.ServerProxy] = None

        # Connection state
        self._connected = False
        self._uid: Optional[int] = None
        self._database: Optional[str] = None
        self._authenticated = False
        self._auth_method: Optional[str] = None  # 'api_key' or 'password'

        logger.info(f"Initialized OdooConnection for {self._url_components['host']}")

    def _parse_url(self, url: str) -> Dict[str, Any]:
        """Parse and validate Odoo URL.

        Args:
            url: The Odoo server URL

        Returns:
            Dictionary with URL components

        Raises:
            OdooConnectionError: If URL is invalid
        """
        try:
            parsed = urlparse(url)

            if parsed.scheme not in ("http", "https"):
                raise OdooConnectionError(
                    f"Invalid URL scheme: {parsed.scheme}. Must be http or https"
                )

            if not parsed.hostname:
                raise OdooConnectionError("Invalid URL: missing hostname")

            port = parsed.port
            if not port:
                port = 443 if parsed.scheme == "https" else 80

            return {
                "scheme": parsed.scheme,
                "host": parsed.hostname,
                "port": port,
                "path": parsed.path.rstrip("/") or "",
                "base_url": url.rstrip("/"),
            }

        except Exception as e:
            raise OdooConnectionError(f"Failed to parse URL: {e}") from e

    def _build_endpoint_url(self, endpoint: str) -> str:
        """Build full URL for an MCP endpoint.

        Args:
            endpoint: The MCP endpoint path

        Returns:
            Full URL for the endpoint
        """
        return f"{self._url_components['base_url']}{endpoint}"

    def connect(self) -> None:
        """Establish connection to Odoo server.

        Creates XML-RPC proxies for MCP endpoints but doesn't
        authenticate yet. Uses connection pooling for better performance.

        Raises:
            OdooConnectionError: If connection fails
        """
        if self._connected:
            logger.warning("Already connected to Odoo")
            return

        try:
            # Use connection pool for proxies with dynamic endpoints
            self._db_proxy = self._performance_manager.get_optimized_connection(self.DB_ENDPOINT)
            self._common_proxy = self._performance_manager.get_optimized_connection(
                self.COMMON_ENDPOINT
            )
            self._object_proxy = self._performance_manager.get_optimized_connection(
                self.OBJECT_ENDPOINT
            )

            # Test connection by calling server_version
            self._test_connection()

            self._connected = True
            logger.info("Successfully connected to Odoo server")

        except socket.timeout:
            raise OdooConnectionError(f"Connection timeout after {self.timeout} seconds") from None
        except socket.error as e:
            raise OdooConnectionError(
                f"Failed to connect to {self._url_components['host']}:"
                f"{self._url_components['port']}: {e}"
            ) from e
        except Exception as e:
            raise OdooConnectionError(f"Connection failed: {e}") from e

    def _test_connection(self) -> None:
        """Test connection by calling server_version.

        Raises:
            OdooConnectionError: If test fails
        """
        try:
            # Try to get server version via common endpoint
            version = self._common_proxy.version()
            logger.debug(f"Server version: {version}")
        except Exception as e:
            raise OdooConnectionError(f"Connection test failed: {e}") from e

    def disconnect(self) -> None:
        """Close connection and cleanup resources."""
        if not self._connected:
            return

        # Clear proxies (but don't close pooled connections)
        self._db_proxy = None
        self._common_proxy = None
        self._object_proxy = None

        # Clear connection state
        self._connected = False
        self._uid = None
        self._database = None
        self._authenticated = False
        self._auth_method = None

        logger.info("Disconnected from Odoo server")

    def check_health(self) -> Tuple[bool, str]:
        """Check connection health.

        Returns:
            Tuple of (is_healthy, status_message)
        """
        if not self._connected:
            return False, "Not connected"

        try:
            # Try to get server version as health check
            version = self._common_proxy.version()
            return True, f"Connected to Odoo {version.get('server_version', 'unknown')}"
        except socket.timeout:
            return False, f"Health check timeout after {self.timeout} seconds"
        except Exception as e:
            return False, f"Health check failed: {e}"

    def test_connection(self) -> bool:
        """Test if connection to Odoo is working.

        Returns:
            True if connection is working, False otherwise
        """
        # If not connected, try to connect first
        if not self._connected:
            try:
                self.connect()
            except Exception as e:
                logger.error(f"Failed to connect: {e}")
                return False

        # Check health
        is_healthy, _ = self.check_health()
        return is_healthy

    def close(self) -> None:
        """Close the connection (alias for disconnect)."""
        self.disconnect()

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._connected

    @property
    def db_proxy(self) -> xmlrpc.client.ServerProxy:
        """Get database operations proxy.

        Returns:
            XML-RPC proxy for database operations

        Raises:
            OdooConnectionError: If not connected
        """
        if not self._connected or not self._db_proxy:
            raise OdooConnectionError("Not connected to Odoo")
        return self._db_proxy

    @property
    def common_proxy(self) -> xmlrpc.client.ServerProxy:
        """Get common operations proxy.

        Returns:
            XML-RPC proxy for common operations

        Raises:
            OdooConnectionError: If not connected
        """
        if not self._connected or not self._common_proxy:
            raise OdooConnectionError("Not connected to Odoo")
        return self._common_proxy

    @property
    def object_proxy(self) -> xmlrpc.client.ServerProxy:
        """Get object operations proxy.

        Returns:
            XML-RPC proxy for object operations

        Raises:
            OdooConnectionError: If not connected
        """
        if not self._connected or not self._object_proxy:
            raise OdooConnectionError("Not connected to Odoo")
        return self._object_proxy

    @property
    def performance_manager(self) -> PerformanceManager:
        """Get the performance manager instance."""
        return self._performance_manager

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
        return False

    def __del__(self):
        """Cleanup on deletion."""
        try:
            # Only disconnect if we're actually connected
            if hasattr(self, "_connected") and self._connected:
                # Suppress logging during cleanup to avoid I/O errors
                self.disconnect()
        except (ValueError, AttributeError, RuntimeError):
            # ValueError: I/O operation on closed file
            # AttributeError: object might be partially initialized
            # RuntimeError: various cleanup-related errors
            pass


@contextmanager
def create_connection(config: OdooConfig, timeout: int = OdooConnection.DEFAULT_TIMEOUT):
    """Create a connection context manager.

    Args:
        config: OdooConfig object
        timeout: Connection timeout in seconds

    Yields:
        Connected OdooConnection instance

    Example:
        with create_connection(config) as conn:
            # Use connection
            version = conn.common_proxy.version()
    """
    conn = OdooConnection(config, timeout)
    try:
        conn.connect()
        yield conn
    finally:
        conn.disconnect()
