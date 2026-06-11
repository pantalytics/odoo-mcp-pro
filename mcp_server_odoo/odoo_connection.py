"""Odoo XML-RPC connection management.

This module provides the OdooConnection class for managing connections
to Odoo via XML-RPC using MCP-specific endpoints.
"""

import json
import logging
import socket
import urllib.error
import urllib.request
import xmlrpc.client
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from .config import OdooConfig
from .error_sanitizer import ErrorSanitizer
from .exceptions import OdooConnectionError  # noqa: F401
from .performance import PerformanceManager

logger = logging.getLogger(__name__)


class OdooConnection:
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

    def list_databases(self) -> List[str]:
        """List all available databases on the Odoo server.

        Returns:
            List of database names

        Raises:
            OdooConnectionError: If listing fails or not connected
        """
        if not self._connected:
            raise OdooConnectionError("Not connected to Odoo")

        try:
            # Call list_db method on database proxy
            databases = self.db_proxy.list()
            logger.info(f"Found {len(databases)} databases: {databases}")
            return databases  # type: ignore[invalid-return-type]  # XML-RPC proxy is untyped
        except xmlrpc.client.Fault as e:
            logger.error(f"Failed to list databases: {e}")
            raise OdooConnectionError(f"Failed to list databases: {e}") from e
        except Exception as e:
            logger.error(f"Failed to list databases: {e}")
            raise OdooConnectionError(f"Failed to list databases: {e}") from e

    def database_exists(self, db_name: str) -> bool:
        """Check if a specific database exists.

        Args:
            db_name: Name of the database to check

        Returns:
            True if database exists, False otherwise

        Raises:
            OdooConnectionError: If check fails
        """
        try:
            databases = self.list_databases()
            return db_name in databases
        except Exception as e:
            logger.error(f"Failed to check database existence: {e}")
            raise OdooConnectionError(f"Failed to check database existence: {e}") from e

    # auto_select_database and _guess_database_from_error removed in v1.2.1
    # Database must be explicitly provided for self-hosted Odoo.
    # Odoo.sh instances don't need a database name (determined by hostname).

    def validate_database_access(self, db_name: str) -> bool:
        """Validate that we can access the specified database.

        This method attempts to authenticate with the database to verify access.

        Args:
            db_name: Name of the database to validate

        Returns:
            True if database is accessible, False otherwise

        Raises:
            OdooConnectionError: If validation fails
        """
        if not self._connected:
            raise OdooConnectionError("Not connected to Odoo")

        try:
            # For API key auth, we'll need to implement a different check
            # For now, we just verify the database exists
            if self.config.uses_api_key:
                # API key validation would be done during actual authentication
                return self.database_exists(db_name)

            # For username/password auth, try to authenticate
            if self.config.uses_credentials:
                # Try to authenticate with the database
                # This will fail if we don't have access
                uid = self.common_proxy.authenticate(
                    db_name, self.config.username, self.config.password, {}
                )
                if uid:
                    logger.info(f"Successfully validated access to database '{db_name}'")
                    return True
                else:
                    logger.warning(f"Authentication failed for database '{db_name}'")
                    return False

            # Should not reach here due to config validation
            raise OdooConnectionError("No authentication method configured")

        except xmlrpc.client.Fault as e:
            logger.error(f"XML-RPC fault validating database access: {e}")
            if "Access Denied" in str(e):
                return False
            raise OdooConnectionError(f"Failed to validate database access: {e}") from e
        except Exception as e:
            logger.error(f"Error validating database access: {e}")
            raise OdooConnectionError(f"Failed to validate database access: {e}") from e

    def _authenticate_api_key_standard(self, database: str) -> bool:
        """Authenticate using API key with standard Odoo XML-RPC.

        Args:
            database: Database name to authenticate against

        Returns:
            True if authentication successful, False otherwise
        """
        if not self.config.username:
            logger.warning("Username required with API key for standard authentication")
            return False

        try:
            # Try exact username first, then lowercase fallback
            # (Odoo web UI lowercases logins, but XML-RPC does exact matching)
            usernames_to_try = [self.config.username]
            lowercase = self.config.username.lower()
            if lowercase != self.config.username:
                usernames_to_try.append(lowercase)

            for username in usernames_to_try:
                uid = self.common_proxy.authenticate(database, username, self.config.api_key, {})
                if uid:
                    self._uid = uid  # type: ignore[invalid-assignment]
                    self._database = database
                    self._auth_method = "api_key"
                    self._authenticated = True
                    logger.info(
                        f"Authenticated using API key as password for user '{username}' (UID: {uid})"
                    )
                    return True

            logger.warning(f"Authentication failed for user '{self.config.username}'")
            return False

        except xmlrpc.client.Fault as e:
            # Handle specific Odoo authentication errors
            fault_string = str(e.faultString).lower()
            if "access denied" in fault_string or "wrong login" in fault_string:
                logger.warning(f"Invalid credentials for user '{self.config.username}'")
            else:
                logger.warning(f"Authentication error: {e.faultString}")
            return False
        except Exception as e:
            logger.error(f"Unexpected authentication error: {e}")
            return False

    def _authenticate_api_key_mcp(self, database: str) -> bool:
        """Authenticate using API key with MCP REST endpoint (standard mode).

        Args:
            database: Database name to authenticate against

        Returns:
            True if authentication successful, False otherwise

        Raises:
            OdooConnectionError: If API request fails critically
        """
        try:
            # Standard MCP API key validation
            url = f"{self._url_components['base_url']}/mcp/auth/validate"

            # Create request with API key header
            req = urllib.request.Request(url)
            req.add_header("X-API-Key", self.config.api_key)

            # Make the request
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))

                if data.get("success") and data.get("data", {}).get("valid"):
                    self._uid = data["data"].get("user_id")
                    self._database = database
                    self._auth_method = "api_key"
                    self._authenticated = True
                    logger.info(f"Successfully authenticated with MCP API key (UID: {self._uid})")
                    return True
                else:
                    logger.warning("MCP API key validation failed")
                    return False

        except urllib.error.HTTPError as e:
            if e.code == 401:
                logger.warning("Invalid MCP API key")
                return False
            elif e.code == 404:
                logger.warning("MCP auth endpoint not found (MCP module may not be installed)")
                return False
            elif e.code == 429:
                logger.warning("Rate limit exceeded during MCP API key validation")
                return False
            else:
                logger.error(f"HTTP error during MCP API key validation: {e}")
                raise OdooConnectionError(f"Failed to validate API key: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            logger.error(f"Network error during MCP API key validation: {e}")
            raise OdooConnectionError(f"Network error during authentication: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error during MCP API key validation: {e}")
            raise OdooConnectionError(f"Failed to validate API key: {e}") from e

    def _authenticate_api_key(self, database: str) -> bool:
        """Authenticate using API key.

        Routes to appropriate authentication method based on mode.

        Args:
            database: Database name to authenticate against

        Returns:
            True if authentication successful, False otherwise

        Raises:
            OdooConnectionError: If API request fails critically
        """
        if not self.config.api_key:
            return False

        # Try MCP module endpoint first, then standard XML-RPC with API key as password
        try:
            if self._authenticate_api_key_mcp(database):
                return True
        except OdooConnectionError:
            logger.info("MCP module auth failed, trying standard XML-RPC API key auth")
        return self._authenticate_api_key_standard(database)

    def _authenticate_password(self, database: str) -> bool:
        """Authenticate using username and password.

        Args:
            database: Database name to authenticate against

        Returns:
            True if authentication successful, False otherwise

        Raises:
            OdooConnectionError: If authentication fails
        """
        if not self.config.username or not self.config.password:
            return False

        try:
            # Use common proxy to authenticate
            uid = self.common_proxy.authenticate(
                database, self.config.username, self.config.password, {}
            )

            if uid:
                self._uid = uid  # type: ignore[invalid-assignment]  # XML-RPC proxy is untyped
                self._database = database
                self._auth_method = "password"
                self._authenticated = True
                logger.info(f"Successfully authenticated with username/password for user ID {uid}")
                return True
            else:
                logger.warning("Username/password authentication failed")
                return False

        except xmlrpc.client.Fault as e:
            logger.warning(f"Authentication fault: {e}")
            return False
        except Exception as e:
            logger.error(f"Error during password authentication: {e}")
            raise OdooConnectionError(f"Failed to authenticate: {e}") from e

    def authenticate(self, database: Optional[str] = None) -> None:
        """Authenticate with Odoo using available credentials.

        Authentication strategy:
        - Try MCP API key first, then fall back to username/password

        Args:
            database: Database name. If not provided, uses auto-selection.

        Raises:
            OdooConnectionError: If authentication fails
        """
        if not self._connected:
            raise OdooConnectionError("Not connected to Odoo")

        # Get database name - no guessing, explicit only
        db_name = database or self.config.database or ""
        if not db_name:
            # For Odoo.sh (*.odoo.com), database is determined by hostname
            url_lower = (self.config.url or "").lower()
            if ".odoo.com" in url_lower:
                # Single-DB instance, authenticate with empty string
                db_name = ""
            else:
                raise OdooConnectionError(
                    "Database name is required for self-hosted Odoo. "
                    "Set it in Advanced settings on the setup page."
                )

        logger.info(f"Authenticating in standard MCP mode for database '{db_name}'")

        auth_errors = []

        # Try API key authentication first (if available)
        if self.config.uses_api_key:
            logger.info("Attempting MCP API key authentication")

            try:
                if self._authenticate_api_key(db_name):
                    logger.info("Successfully authenticated using MCP API key")
                    return
                else:
                    error_msg = "MCP API key authentication failed"
                    auth_errors.append(error_msg)

                    # Only try fallback if we have credentials
                    if self.config.uses_credentials:
                        logger.info(f"{error_msg}, trying username/password fallback")
            except OdooConnectionError as e:
                # Critical error (network, etc.) - don't try fallback
                logger.error(f"Critical error during MCP API key authentication: {e}")
                raise

        # Try username/password authentication (if available)
        if self.config.uses_credentials:
            logger.info("Attempting username/password authentication")

            try:
                if self._authenticate_password(db_name):
                    logger.info("Successfully authenticated using username/password")
                    return
                else:
                    auth_errors.append("Username/password authentication failed")
            except OdooConnectionError as e:
                # Critical error - propagate it
                logger.error(f"Critical error during password authentication: {e}")
                raise

        # Authentication failed - provide helpful error message
        if auth_errors:
            raise OdooConnectionError(
                "Authentication failed. Please check: "
                "(1) your API key is valid and not expired, "
                "(2) your database name is correct (if self-hosted), "
                "(3) your Odoo login email matches your sign-up email (for Odoo 14-18)."
            )
        else:
            raise OdooConnectionError(
                "No authentication method configured. Please provide an API key in the setup page."
            )

    @property
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return self._authenticated

    @property
    def uid(self) -> Optional[int]:
        """Get authenticated user ID."""
        return self._uid

    @property
    def database(self) -> Optional[str]:
        """Get authenticated database name."""
        return self._database

    @property
    def auth_method(self) -> Optional[str]:
        """Get authentication method used ('api_key' or 'password')."""
        return self._auth_method

    @property
    def performance_manager(self) -> PerformanceManager:
        """Get the performance manager instance."""
        return self._performance_manager

    def execute(self, model: str, method: str, *args) -> Any:
        """Execute an operation on an Odoo model.

        This is a simplified interface that calls execute_kw with empty kwargs.

        Args:
            model: The Odoo model name (e.g., 'res.partner')
            method: The method to call (e.g., 'search', 'read')
            *args: Arguments to pass to the method

        Returns:
            The result from Odoo

        Raises:
            OdooConnectionError: If not authenticated or execution fails
        """
        return self.execute_kw(model, method, list(args), {})

    def call_method(
        self,
        model: str,
        method: str,
        ids: Optional[List[int]] = None,
        **kwargs: Any,
    ) -> Any:
        """Transport-agnostic recordset method call (XML-RPC backend).

        See `OdooConnectionProtocol.call_method` for semantics.
        """
        args = [list(ids)] if ids is not None else []
        return self.execute_kw(model, method, args, kwargs)

    def execute_kw(self, model: str, method: str, args: List[Any], kwargs: Dict[str, Any]) -> Any:
        """Execute an operation on an Odoo model with keyword arguments.

        This is the main method for interacting with Odoo models via XML-RPC.

        Args:
            model: The Odoo model name (e.g., 'res.partner')
            method: The method to call (e.g., 'search_read')
            args: List of positional arguments for the method
            kwargs: Dictionary of keyword arguments for the method

        Returns:
            The result from Odoo

        Raises:
            OdooConnectionError: If not authenticated or execution fails
        """
        if not self._authenticated:
            raise OdooConnectionError("Not authenticated. Call authenticate() first.")

        if not self._connected:
            raise OdooConnectionError("Not connected to Odoo")

        # Get the appropriate password/token based on auth method
        password_or_token = (
            self.config.api_key if self._auth_method == "api_key" else self.config.password
        )

        try:
            # Log the operation
            logger.debug(f"Executing {method} on {model} with args={args}, kwargs={kwargs}")

            # Execute via object proxy
            result = self.object_proxy.execute_kw(
                self._database, self._uid, password_or_token, model, method, args, kwargs
            )

            logger.debug("Operation completed successfully")
            return result

        except xmlrpc.client.Fault as e:
            logger.error(f"XML-RPC fault during {method} on {model}: {e}")
            # Sanitize the fault string before exposing to user
            sanitized_message = ErrorSanitizer.sanitize_xmlrpc_fault(e.faultString)
            raise OdooConnectionError(f"Operation failed: {sanitized_message}") from e
        except socket.timeout:
            logger.error(f"Timeout during {method} on {model}")
            raise OdooConnectionError(f"Operation timeout after {self.timeout} seconds") from None
        except Exception as e:
            logger.error(f"Error during {method} on {model}: {e}")
            # Sanitize generic errors as well
            sanitized_message = ErrorSanitizer.sanitize_message(str(e))
            raise OdooConnectionError(f"Operation failed: {sanitized_message}") from e

    def search(self, model: str, domain: List[Union[str, List[Any]]], **kwargs) -> List[int]:
        """Search for records matching a domain.

        Args:
            model: The Odoo model name
            domain: Odoo domain filter (e.g., [['is_company', '=', True]])
            **kwargs: Additional parameters (limit, offset, order)

        Returns:
            List of record IDs matching the domain
        """
        return self.execute_kw(model, "search", [domain], kwargs)

    def read(
        self, model: str, ids: List[int], fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Read records by IDs.

        Args:
            model: The Odoo model name
            ids: List of record IDs to read
            fields: List of field names to read (None for all fields)

        Returns:
            List of dictionaries containing record data
        """
        kwargs = {}
        if fields:
            kwargs["fields"] = fields

        with self._performance_manager.monitor.track_operation(f"read_{model}"):
            records = self.execute_kw(model, "read", [ids], kwargs)

        return records

    def search_read(
        self,
        model: str,
        domain: List[Union[str, List[Any]]],
        fields: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Search for records and read their data in one operation.

        Args:
            model: The Odoo model name
            domain: Odoo domain filter
            fields: List of field names to read (None for all fields)
            **kwargs: Additional parameters (limit, offset, order)

        Returns:
            List of dictionaries containing record data
        """
        if fields:
            kwargs["fields"] = fields
        return self.execute_kw(model, "search_read", [domain], kwargs)

    def fields_get(
        self, model: str, attributes: Optional[List[str]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get field definitions for a model.

        Args:
            model: The Odoo model name
            attributes: List of field attributes to return

        Returns:
            Dictionary mapping field names to their definitions
        """
        # Check cache first
        cached_fields = self._performance_manager.get_cached_fields(model)
        if cached_fields and not attributes:  # Only use cache if no specific attributes requested
            logger.debug(f"Field definitions for {model} retrieved from cache")
            return cached_fields

        # Get fields from server
        kwargs = {}
        if attributes:
            kwargs["attributes"] = attributes

        with self._performance_manager.monitor.track_operation(f"fields_get_{model}"):
            fields = self.execute_kw(model, "fields_get", [], kwargs)

        # Cache if we got all attributes
        if not attributes:
            self._performance_manager.cache_fields(model, fields)

        return fields

    def search_count(self, model: str, domain: List[Union[str, List[Any]]]) -> int:
        """Count records matching a domain.

        Args:
            model: The Odoo model name
            domain: Odoo domain filter

        Returns:
            Number of records matching the domain
        """
        return self.execute_kw(model, "search_count", [domain], {})

    def create(self, model: str, values: Dict[str, Any]) -> int:
        """Create a new record.

        Args:
            model: The Odoo model name
            values: Dictionary of field values for the new record

        Returns:
            ID of the created record

        Raises:
            OdooConnectionError: If creation fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"create_{model}"):
                record_id = self.execute_kw(model, "create", [values], {})
                # Invalidate cache for this model
                self._performance_manager.invalidate_record_cache(model)
                logger.info(f"Created {model} record with ID {record_id}")
                return record_id
        except Exception as e:
            logger.error(f"Failed to create {model} record: {e}")
            raise

    def create_bulk(self, model: str, vals_list: List[Dict[str, Any]]) -> List[int]:
        """Create multiple records in a single call.

        Args:
            model: Odoo model name
            vals_list: List of dicts, each containing field values for one record

        Returns:
            List of IDs of the created records

        Raises:
            OdooConnectionError: If creation fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"create_bulk_{model}"):
                result = self.execute_kw(model, "create", [vals_list], {})
                self._performance_manager.invalidate_record_cache(model)
                if not isinstance(result, list):
                    result = [result]
                logger.info(f"Bulk created {len(result)} {model} record(s)")
                return result
        except Exception as e:
            logger.error(f"Failed to bulk create {model} records: {e}")
            raise

    def load_records(
        self,
        model: str,
        fields: List[str],
        data: List[List[str]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Import records using Odoo's load() method with external ID support.

        This wraps Odoo's native load() ORM method, which supports:
        - Idempotent upsert via external IDs (column name 'id')
        - Relational field resolution via external IDs (e.g., 'parent_id/id')
        - Batch processing with per-row error reporting

        Args:
            model: Odoo model name (e.g., 'res.partner')
            fields: List of field names. Use 'id' for external ID column.
                    Use 'field/id' to reference related records by external ID.
            data: List of rows, each row is a list of string values.
                  Values must be strings (Odoo load() expects strings).
            context: Optional context dict (e.g., {'tracking_disable': True})

        Returns:
            Dict with 'ids' (created/updated record IDs) and 'messages' (errors)

        Raises:
            OdooConnectionError: If the load operation fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"load_{model}"):
                kwargs = {}
                if context:
                    kwargs["context"] = context
                result = self.execute_kw(model, "load", [fields, data], kwargs)
                self._performance_manager.invalidate_record_cache(model)
                logger.info(
                    f"Loaded {len(data)} row(s) into {model}: "
                    f"{len(result.get('ids', []))} OK, "
                    f"{len(result.get('messages', []))} messages"
                )
                return result
        except Exception as e:
            logger.error(f"Failed to load records into {model}: {e}")
            raise

    def write(self, model: str, ids: List[int], values: Dict[str, Any]) -> bool:
        """Update existing records.

        Args:
            model: The Odoo model name
            ids: List of record IDs to update
            values: Dictionary of field values to update

        Returns:
            True if update was successful

        Raises:
            OdooConnectionError: If update fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"write_{model}"):
                result = self.execute_kw(model, "write", [ids, values], {})
                # Invalidate cache for updated records
                for record_id in ids:
                    self._performance_manager.invalidate_record_cache(model, record_id)
                logger.info(f"Updated {len(ids)} {model} record(s)")
                return result
        except Exception as e:
            logger.error(f"Failed to update {model} records: {e}")
            raise

    def unlink(self, model: str, ids: List[int]) -> bool:
        """Delete records.

        Args:
            model: The Odoo model name
            ids: List of record IDs to delete

        Returns:
            True if deletion was successful

        Raises:
            OdooConnectionError: If deletion fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"unlink_{model}"):
                result = self.execute_kw(model, "unlink", [ids], {})
                # Invalidate cache for deleted records
                for record_id in ids:
                    self._performance_manager.invalidate_record_cache(model, record_id)
                logger.info(f"Deleted {len(ids)} {model} record(s)")
                return result
        except Exception as e:
            logger.error(f"Failed to delete {model} records: {e}")
            raise

    def check_access_rights(self, model: str, operation: str) -> bool:
        """Check if the current user has the given access right on a model.

        Args:
            model: Odoo model name (e.g., 'res.partner')
            operation: One of 'read', 'write', 'create', 'unlink'

        Returns:
            True if access is granted, False if denied or on error
        """
        try:
            result = self.execute_kw(
                model, "check_access_rights", [operation], {"raise_exception": False}
            )
            return bool(result)
        except Exception as e:
            # If model doesn't exist (module not installed), assume no access
            error_str = str(e).lower()
            if "doesn't exist" in error_str or "does not exist" in error_str:
                return False
            # For other errors (network, etc.), assume access is granted
            # and let the actual operation fail with a clear error
            return True

    def get_server_version(self) -> Optional[Dict[str, Any]]:
        """Get Odoo server version information.

        Returns:
            Dictionary with version information or None if not connected
        """
        if not self._connected:
            return None

        try:
            return self.common_proxy.version()  # type: ignore[invalid-return-type]  # XML-RPC proxy is untyped
        except Exception as e:
            logger.error(f"Failed to get server version: {e}")
            return None


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
