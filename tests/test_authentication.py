"""Tests for authentication functionality in OdooConnection.

This module tests both API key and username/password authentication flows.
"""

import json
import os
import urllib.error
from unittest.mock import MagicMock, Mock, patch
from xmlrpc.client import Fault

import pytest

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_connection import OdooConnection, OdooConnectionError

from .conftest import ODOO_SERVER_AVAILABLE


class TestAuthentication:
    """Test authentication functionality."""

    @pytest.fixture
    def config_api_key(self):
        """Create configuration with API key."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="test_api_key",
            database=os.getenv("ODOO_DB"),
        )

    @pytest.fixture
    def config_password(self):
        """Create configuration with username/password."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            username=os.getenv("ODOO_USER", "admin"),
            password=os.getenv("ODOO_PASSWORD", "admin"),
            database=os.getenv("ODOO_DB"),
        )

    @pytest.fixture
    def config_both(self):
        """Create configuration with both auth methods."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="test_api_key",
            username=os.getenv("ODOO_USER", "admin"),
            password=os.getenv("ODOO_PASSWORD", "admin"),
            database=os.getenv("ODOO_DB"),
        )

    @pytest.fixture
    def connection_api_key(self, config_api_key):
        """Create connection with API key config."""
        conn = OdooConnection(config_api_key)
        conn._connected = True
        return conn

    @pytest.fixture
    def connection_password(self, config_password):
        """Create connection with password config."""
        conn = OdooConnection(config_password)
        conn._connected = True
        return conn

    def test_authenticate_not_connected(self, config_api_key):
        """Test authenticate raises error when not connected."""
        conn = OdooConnection(config_api_key)
        with pytest.raises(OdooConnectionError, match="Not connected"):
            conn.authenticate()

    @patch("urllib.request.urlopen")
    def test_api_key_authentication_success(self, mock_urlopen, connection_api_key):
        """Test successful API key authentication."""
        # Mock successful API response
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"success": True, "data": {"valid": True, "user_id": 2}}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Authenticate
        connection_api_key.authenticate("mcp")

        # Verify authentication state
        assert connection_api_key.is_authenticated
        assert connection_api_key.uid == 2
        assert connection_api_key.database == "mcp"
        assert connection_api_key.auth_method == "api_key"

    @patch("urllib.request.urlopen")
    def test_api_key_authentication_invalid(self, mock_urlopen, connection_api_key):
        """Test API key authentication with invalid key."""
        # Mock 401 response
        mock_urlopen.side_effect = urllib.error.HTTPError(None, 401, "Unauthorized", {}, None)

        # Should raise error since no fallback
        with pytest.raises(OdooConnectionError, match="Authentication failed"):
            connection_api_key.authenticate("mcp")

        # Verify not authenticated
        assert not connection_api_key.is_authenticated

    def test_password_authentication_success(self, connection_password):
        """Test successful username/password authentication."""
        # Mock common proxy
        mock_common = Mock()
        mock_common.authenticate.return_value = 2
        connection_password._common_proxy = mock_common

        # Authenticate
        connection_password.authenticate("mcp")

        # Verify authentication state
        assert connection_password.is_authenticated
        assert connection_password.uid == 2
        assert connection_password.database == "mcp"
        assert connection_password.auth_method == "password"

        # Verify authenticate was called correctly
        mock_common.authenticate.assert_called_once_with(
            "mcp", os.getenv("ODOO_USER", "admin"), os.getenv("ODOO_PASSWORD", "admin"), {}
        )

    def test_password_authentication_failed(self, connection_password):
        """Test failed username/password authentication."""
        # Mock common proxy
        mock_common = Mock()
        mock_common.authenticate.return_value = False
        connection_password._common_proxy = mock_common

        # Should raise error
        with pytest.raises(OdooConnectionError, match="Authentication failed"):
            connection_password.authenticate("mcp")

        # Verify not authenticated
        assert not connection_password.is_authenticated

    def test_password_authentication_fault(self, connection_password):
        """Test username/password authentication with XML-RPC fault."""
        # Mock common proxy
        mock_common = Mock()
        mock_common.authenticate.side_effect = Fault(1, "Access Denied")
        connection_password._common_proxy = mock_common

        # Should raise error
        with pytest.raises(OdooConnectionError, match="Authentication failed"):
            connection_password.authenticate("mcp")

        # Verify not authenticated
        assert not connection_password.is_authenticated

    @patch("urllib.request.urlopen")
    def test_authentication_fallback(self, mock_urlopen, config_both):
        """Test fallback from MCP /auth/validate to standard XML-RPC.

        When the MCP-specific endpoint returns 401, _authenticate_api_key
        falls back to _authenticate_api_key_standard which calls common.authenticate
        with the api_key in the password slot. The auth_method stays "api_key"
        because that's literally what was used — this is NOT a fallback to
        username/password auth (that path is covered by
        test_authentication_fallback_in_standard_mode).
        """
        conn = OdooConnection(config_both)
        conn._connected = True

        # MCP endpoint returns 401 → triggers XML-RPC fallback
        mock_urlopen.side_effect = urllib.error.HTTPError(None, 401, "Unauthorized", {}, None)

        # Standard XML-RPC accepts the api_key as password and returns a uid
        mock_common = Mock()
        mock_common.authenticate.return_value = 3
        conn._common_proxy = mock_common

        conn.authenticate("mcp")

        assert conn.is_authenticated
        assert conn.uid == 3
        assert conn.auth_method == "api_key"

    def test_authentication_state_cleared_on_disconnect(self, connection_api_key):
        """Test authentication state is cleared on disconnect."""
        # Set authentication state
        connection_api_key._authenticated = True
        connection_api_key._uid = 2
        connection_api_key._database = "mcp"
        connection_api_key._auth_method = "api_key"

        # Disconnect
        connection_api_key.disconnect()

        # Verify state cleared
        assert not connection_api_key.is_authenticated
        assert connection_api_key.uid is None
        assert connection_api_key.database is None
        assert connection_api_key.auth_method is None


@pytest.mark.skipif(not ODOO_SERVER_AVAILABLE, reason="Odoo server not available")
@pytest.mark.xmlrpc_only
class TestAuthenticationIntegration:
    """Integration tests with real Odoo server."""

    @pytest.fixture
    def real_config_api_key(self):
        """Create configuration with real API key."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key=os.getenv("ODOO_API_KEY"),
            database=None,  # Let it auto-select
        )

    @pytest.fixture
    def real_config_password(self):
        """Create configuration with username/password."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            username=os.getenv("ODOO_USER", "admin"),
            password=os.getenv("ODOO_PASSWORD", "admin"),
            database=None,  # Let it auto-select
        )

    def test_real_api_key_authentication(self, real_config_api_key):
        """Test API key authentication with real server."""
        with OdooConnection(real_config_api_key) as conn:
            # Authenticate
            conn.authenticate()

            # Verify authenticated
            assert conn.is_authenticated
            assert conn.uid is not None
            assert conn.database is not None
            assert conn.auth_method == "api_key"

            print(f"Authenticated with API key: uid={conn.uid}, db={conn.database}")

    def test_real_password_authentication(self, real_config_password):
        """Test username/password authentication with real server."""
        with OdooConnection(real_config_password) as conn:
            # Authenticate
            conn.authenticate()

            # Verify authenticated
            assert conn.is_authenticated
            assert conn.uid is not None
            assert conn.database is not None
            assert conn.auth_method == "password"

            print(f"Authenticated with password: uid={conn.uid}, db={conn.database}")

    def test_real_invalid_api_key(self):
        """Test authentication with invalid API key."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="invalid_key_12345",
            database=os.getenv("ODOO_DB"),
        )

        with OdooConnection(config) as conn:
            with pytest.raises(OdooConnectionError, match="Authentication failed"):
                conn.authenticate()

    def test_real_invalid_password(self):
        """Test authentication with invalid password."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            username=os.getenv("ODOO_USER", "admin"),
            password="wrong_password",
            database=os.getenv("ODOO_DB"),
        )

        with OdooConnection(config) as conn:
            with pytest.raises(OdooConnectionError, match="Authentication failed"):
                conn.authenticate()


class TestAuthenticationRouting:
    """Test authentication routing and error handling."""

    def test_standard_mode_endpoints(self):
        """Test that standard mode uses MCP endpoints."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="test_api_key",
            database=os.getenv("ODOO_DB"),
        )
        conn = OdooConnection(config)

        # Check that MCP endpoints are used
        assert conn.DB_ENDPOINT == "/xmlrpc/2/db"
        assert conn.COMMON_ENDPOINT == "/xmlrpc/2/common"
        assert conn.OBJECT_ENDPOINT == "/xmlrpc/2/object"

    def test_authentication_routing_standard_mode(self):
        """Test that standard mode routes to MCP authentication."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="test_api_key",
            database=os.getenv("ODOO_DB"),
        )
        conn = OdooConnection(config)
        conn._connected = True

        # Mock the MCP authentication method
        with patch.object(conn, "_authenticate_api_key_mcp", return_value=True) as mock_mcp:
            with patch.object(
                conn, "_authenticate_api_key_standard", return_value=False
            ) as mock_std:
                success = conn._authenticate_api_key("testdb")

                # Should call MCP method, not standard
                mock_mcp.assert_called_once_with("testdb")
                mock_std.assert_not_called()
                assert success is True

    def test_authentication_fallback_in_standard_mode(self):
        """Test fallback from API key to password in standard mode."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="test_api_key",
            username=os.getenv("ODOO_USER", "admin"),
            password=os.getenv("ODOO_PASSWORD", "admin"),
            database=os.getenv("ODOO_DB"),
        )
        conn = OdooConnection(config)
        conn._connected = True

        # Mock database operations
        mock_db = Mock()
        mock_db.list.return_value = ["testdb"]
        conn._db_proxy = mock_db

        # Mock authentication methods
        with patch.object(conn, "_authenticate_api_key", return_value=False):
            with patch.object(conn, "_authenticate_password", return_value=True) as mock_pwd:
                # Set authentication state when password auth succeeds
                def set_auth_state(db):
                    conn._authenticated = True
                    conn._uid = 2
                    conn._database = db
                    conn._auth_method = "password"
                    return True

                mock_pwd.side_effect = set_auth_state

                # Should fallback to password auth
                conn.authenticate("testdb")

                mock_pwd.assert_called_once_with("testdb")
                assert conn.is_authenticated

    def test_authentication_error_messages(self):
        """Test detailed error messages for authentication failures."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="test_api_key",
            username=os.getenv("ODOO_USER", "admin"),
            password=os.getenv("ODOO_PASSWORD", "admin"),
            database=os.getenv("ODOO_DB"),
        )
        conn = OdooConnection(config)
        conn._connected = True

        # Mock database operations
        mock_db = Mock()
        mock_db.list.return_value = ["testdb"]
        conn._db_proxy = mock_db

        # Mock all authentication methods to fail
        with patch.object(conn, "_authenticate_api_key", return_value=False):
            with patch.object(conn, "_authenticate_password", return_value=False):
                # Should raise detailed error
                with pytest.raises(OdooConnectionError) as exc_info:
                    conn.authenticate("testdb")

                error_msg = str(exc_info.value)
                assert "Authentication failed" in error_msg
                assert "API key" in error_msg


if __name__ == "__main__":
    # Run integration tests when executed directly
    pytest.main([__file__, "-v", "-k", "Integration"])
