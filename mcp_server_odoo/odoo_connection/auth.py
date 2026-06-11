"""Authentication and database-access methods for OdooConnection.

This module provides OdooConnectionAuthMixin, a plain mixin class that
implements database listing/validation and the API-key / password
authentication flows. All instance attributes it references are
initialized in `core.OdooConnection.__init__`.
"""

import json
import logging
import urllib.error
import urllib.request
import xmlrpc.client
from typing import List, Optional

from ..exceptions import OdooConnectionError

logger = logging.getLogger(__name__)


class OdooConnectionAuthMixin:
    """Mixin with authentication and database-access methods."""

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
