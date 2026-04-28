"""Tests for database discovery functionality in OdooConnection.

This module tests database listing, auto-selection logic, and
database validation features.
"""

import os
from unittest.mock import Mock
from xmlrpc.client import Fault

import pytest

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_connection import OdooConnection, OdooConnectionError

from .conftest import ODOO_SERVER_AVAILABLE


class TestDatabaseDiscovery:
    """Test database discovery and auto-selection functionality."""

    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="test_api_key",
            database=None,  # No database specified for auto-selection tests
        )

    @pytest.fixture
    def config_with_db(self):
        """Create test configuration with database specified."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="test_api_key",
            database=os.getenv("ODOO_DB"),
        )

    @pytest.fixture
    def connection(self, config):
        """Create OdooConnection instance."""
        return OdooConnection(config)

    def test_list_databases_not_connected(self, connection):
        """Test list_databases raises error when not connected."""
        with pytest.raises(OdooConnectionError, match="Not connected"):
            connection.list_databases()

    def test_list_databases_success(self, connection):
        """Test successful database listing."""
        connection._connected = True
        mock_proxy = Mock()
        mock_proxy.list.return_value = ["db1", "db2", os.getenv("ODOO_DB", "db")]
        connection._db_proxy = mock_proxy

        databases = connection.list_databases()

        assert databases == ["db1", "db2", os.getenv("ODOO_DB", "db")]
        mock_proxy.list.assert_called_once()

    def test_list_databases_error(self, connection):
        """Test database listing with server error."""
        connection._connected = True
        mock_proxy = Mock()
        mock_proxy.list.side_effect = Exception("Server error")
        connection._db_proxy = mock_proxy

        with pytest.raises(OdooConnectionError, match="Failed to list databases"):
            connection.list_databases()

    def test_database_exists_true(self, connection):
        """Test database_exists returns True for existing database."""
        connection._connected = True
        mock_proxy = Mock()
        mock_proxy.list.return_value = ["db1", os.getenv("ODOO_DB", "db"), "test"]
        connection._db_proxy = mock_proxy

        assert connection.database_exists(os.getenv("ODOO_DB", "db")) is True
        assert connection.database_exists("test") is True

    def test_database_exists_false(self, connection):
        """Test database_exists returns False for non-existing database."""
        connection._connected = True
        mock_proxy = Mock()
        mock_proxy.list.return_value = ["db1", os.getenv("ODOO_DB", "db")]
        connection._db_proxy = mock_proxy

        assert connection.database_exists("nonexistent") is False

    # auto_select_database tests removed — the method itself was removed in v1.2.1
    # (database must now be explicitly provided for self-hosted Odoo).

    def test_validate_database_access_api_key(self, connection):
        """Test database validation with API key authentication."""
        connection._connected = True
        mock_proxy = Mock()
        mock_proxy.list.return_value = [os.getenv("ODOO_DB", "db"), "test"]
        connection._db_proxy = mock_proxy

        # Should just check existence for API key auth
        assert connection.validate_database_access(os.getenv("ODOO_DB", "db")) is True
        assert connection.validate_database_access("nonexistent") is False

    def test_validate_database_access_credentials(self):
        """Test database validation with username/password authentication."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            username=os.getenv("ODOO_USER", "admin"),
            password=os.getenv("ODOO_PASSWORD", "admin"),
        )
        connection = OdooConnection(config)
        connection._connected = True

        mock_common = Mock()
        mock_common.authenticate.return_value = 2  # User ID
        connection._common_proxy = mock_common

        assert connection.validate_database_access(os.getenv("ODOO_DB", "db")) is True
        mock_common.authenticate.assert_called_once_with(
            os.getenv("ODOO_DB", "db"),
            os.getenv("ODOO_USER", "admin"),
            os.getenv("ODOO_PASSWORD", "admin"),
            {},
        )

    def test_validate_database_access_auth_failed(self):
        """Test database validation with failed authentication."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            username=os.getenv("ODOO_USER", "admin"),
            password="wrong",
        )
        connection = OdooConnection(config)
        connection._connected = True

        mock_common = Mock()
        mock_common.authenticate.return_value = False
        connection._common_proxy = mock_common

        assert connection.validate_database_access(os.getenv("ODOO_DB", "db")) is False

    def test_validate_database_access_fault(self):
        """Test database validation with XML-RPC fault."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            username=os.getenv("ODOO_USER", "admin"),
            password=os.getenv("ODOO_PASSWORD", "admin"),
        )
        connection = OdooConnection(config)
        connection._connected = True

        mock_common = Mock()
        mock_common.authenticate.side_effect = Fault(1, "Access Denied")
        connection._common_proxy = mock_common

        assert connection.validate_database_access(os.getenv("ODOO_DB", "db")) is False


@pytest.mark.skipif(not ODOO_SERVER_AVAILABLE, reason="Odoo server not available")
@pytest.mark.xmlrpc_only
class TestDatabaseDiscoveryIntegration:
    """Integration tests with real Odoo server."""

    @pytest.fixture
    def real_config(self):
        """Create configuration for real Odoo server."""
        # Use hardcoded values for local test server
        # Don't load from environment to avoid conflicts
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key=os.getenv("ODOO_API_KEY"),
            database=None,  # Let it auto-select
        )

    def test_real_list_databases(self, real_config):
        """Test listing databases on real Odoo server."""
        with OdooConnection(real_config) as conn:
            databases = conn.list_databases()

            # Should have at least one database
            assert isinstance(databases, list)
            assert len(databases) > 0
            print(f"Found databases: {databases}")

    def test_real_validate_access(self, real_config):
        """Test database access validation on real server."""
        with OdooConnection(real_config) as conn:
            # Get a database to test
            databases = conn.list_databases()
            if databases:
                db_name = databases[0]

                # Should be able to validate access
                result = conn.validate_database_access(db_name)
                assert isinstance(result, bool)
                print(f"Access to '{db_name}': {result}")


if __name__ == "__main__":
    # Run integration tests when executed directly
    pytest.main([__file__, "-v", "-k", "Integration"])
