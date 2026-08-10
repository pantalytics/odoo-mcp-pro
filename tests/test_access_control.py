"""Tests for access control via Odoo's check_access_rights.

Tests the AccessController class which uses Odoo's native check_access_rights
for both JSON/2 and XML-RPC connections.
"""

import os
import threading
from unittest.mock import MagicMock

import pytest

from mcp_server_odoo.access_control import (
    AccessControlError,
    AccessController,
)
from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.exceptions import OdooConnectionError, OdooTimeoutError
from mcp_server_odoo.odoo_connection.orm import OdooConnectionOrmMixin
from mcp_server_odoo.odoo_json2_orm import Json2OrmMixin
from mcp_server_odoo.tools._common import validate_access

from .conftest import ODOO_SERVER_AVAILABLE


class TestAccessControl:
    """Test core access control functionality."""

    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="test_api_key",
            database=os.getenv("ODOO_DB"),
        )

    @pytest.fixture
    def controller(self, config):
        """Create AccessController instance."""
        return AccessController(config, cache_ttl=60)

    def test_init_without_connection_allows_all(self):
        """Test initialization without connection delegates to Odoo."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            username=os.getenv("ODOO_USER", "admin"),
            password=os.getenv("ODOO_PASSWORD", "admin"),
            database=os.getenv("ODOO_DB"),
        )

        ctrl = AccessController(config)
        # Without connection, all operations are allowed (Odoo handles ACLs)
        perms = ctrl.get_model_permissions("res.partner")
        assert perms.can_read is True

    def test_cache_operations(self, controller):
        """Test cache get/set operations."""
        # Test cache miss
        assert controller._get_from_cache("test_key") is None

        # Test cache set and hit
        controller._set_cache("test_key", {"data": "value"})
        assert controller._get_from_cache("test_key") == {"data": "value"}

        # Test cache clear
        controller.clear_cache()
        assert controller._get_from_cache("test_key") is None

    def test_cache_expiration(self, controller):
        """Test cache expiration."""
        # Set cache with short TTL
        controller.cache_ttl = 0  # Immediate expiration
        controller._set_cache("test_key", "value")

        # Should be expired
        assert controller._get_from_cache("test_key") is None

    def test_get_enabled_models_returns_empty(self, controller):
        """All models are accessible — Odoo handles ACLs."""
        assert controller.get_enabled_models() == []


@pytest.mark.skipif(not ODOO_SERVER_AVAILABLE, reason="Odoo server not available")
@pytest.mark.xmlrpc_only
class TestAccessControlIntegration:
    """Integration tests with real Odoo server."""

    @pytest.fixture
    def real_config(self):
        """Create configuration with real credentials."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key=os.getenv("ODOO_API_KEY"),
            database=os.getenv("ODOO_DB"),
        )

    def test_real_get_enabled_models(self, real_config):
        """Test getting enabled models from real server."""
        controller = AccessController(real_config)

        models = controller.get_enabled_models()

        assert isinstance(models, list)
        print(f"Found {len(models)} enabled models")

        # Just verify we got some models
        if models:
            # Print first few models as example
            for model in models[:3]:
                print(f"  - {model.get('model', 'unknown')}")

    def test_real_model_permissions(self, real_config, readable_model):
        """Test getting model permissions from real server."""
        controller = AccessController(real_config)

        # Use the discovered readable model
        model_name = readable_model.model

        # Get model permissions
        perms = controller.get_model_permissions(model_name)

        assert perms.model == model_name
        assert perms.enabled is True
        assert perms.can_read is True  # We specifically requested a readable model
        print(
            f"{model_name} permissions: read={perms.can_read}, "
            f"write={perms.can_write}, create={perms.can_create}, "
            f"unlink={perms.can_unlink}"
        )

    def test_real_check_operations(self, real_config, readable_model, disabled_model):
        """Test checking operations on real server."""
        controller = AccessController(real_config)

        # Check enabled model operations
        allowed, msg = controller.check_operation_allowed(readable_model.model, "read")
        print(f"{readable_model.model} read: allowed={allowed}, msg={msg}")
        assert allowed is True

        # Check a model we know is not enabled
        allowed, msg = controller.check_operation_allowed(disabled_model, "read")
        print(f"{disabled_model} read: allowed={allowed}, msg={msg}")
        assert allowed is False

    def test_real_validate_access(self, real_config, readable_model, disabled_model):
        """Test access validation on real server."""
        controller = AccessController(real_config)

        # Should not raise for enabled model with permission
        try:
            controller.validate_model_access(readable_model.model, "read")
            print(f"{readable_model.model} read access validated")
        except AccessControlError as e:
            print(f"{readable_model.model} read access denied: {e}")

        # Should raise for non-enabled model
        with pytest.raises(AccessControlError):
            controller.validate_model_access(disabled_model, "read")

    def test_real_cache_performance(self, real_config):
        """Test cache improves performance."""
        controller = AccessController(real_config)

        import time

        # First call - no cache
        start = time.time()
        models1 = controller.get_enabled_models()
        time1 = time.time() - start

        # Second call - from cache
        start = time.time()
        models2 = controller.get_enabled_models()
        time2 = time.time() - start

        assert models1 == models2
        assert time2 < time1  # Cache should be faster
        print(f"First call: {time1:.3f}s, Cached call: {time2:.3f}s")

    def test_real_all_permissions(self, real_config):
        """Test getting all permissions from real server."""
        controller = AccessController(real_config)

        all_perms = controller.get_all_permissions()

        print(f"Retrieved permissions for {len(all_perms)} models")

        # Print a sample
        for model, perms in list(all_perms.items())[:3]:
            print(f"{model}: read={perms.can_read}, write={perms.can_write}")


class TestAccessControlJSON2:
    """Tests for AccessController in JSON/2 mode using Odoo's check_access_rights."""

    @pytest.fixture
    def json2_config(self):
        return OdooConfig(
            url="http://localhost:8069",
            api_key="test_key",
            database="testdb",
            api_version="json2",
        )

    def _connection(self, access_rights):
        """Mock connection with check_access_rights based on a dict."""
        conn = MagicMock()
        conn.check_access_rights.side_effect = lambda model, op: access_rights.get(
            (model, op), False
        )
        return conn

    def test_init_with_connection(self, json2_config):
        conn = MagicMock()
        ctrl = AccessController(json2_config, connection=conn)
        assert ctrl.connection is conn

    def test_init_without_connection(self, json2_config):
        ctrl = AccessController(json2_config)
        assert ctrl.connection is None

    def test_get_model_permissions_uses_check_access_rights(self, json2_config):
        conn = self._connection(
            {
                ("res.partner", "read"): True,
                ("res.partner", "write"): True,
                ("res.partner", "create"): True,
                ("res.partner", "unlink"): False,
            }
        )
        ctrl = AccessController(json2_config, connection=conn)

        perms = ctrl.get_model_permissions("res.partner")

        assert perms.can_read is True
        assert perms.can_write is True
        assert perms.can_create is True
        assert perms.can_unlink is False
        assert conn.check_access_rights.call_count == 4

    def test_get_model_permissions_no_access(self, json2_config):
        conn = self._connection({})  # all False by default
        ctrl = AccessController(json2_config, connection=conn)

        perms = ctrl.get_model_permissions("sale.order")

        assert perms.can_read is False
        assert perms.enabled is False

    def test_get_model_permissions_no_connection_allows_all(self, json2_config):
        ctrl = AccessController(json2_config)  # no connection

        perms = ctrl.get_model_permissions("res.partner")

        assert perms.can_read is True
        assert perms.can_write is True
        assert perms.can_create is True
        assert perms.can_unlink is True

    def test_is_model_enabled_with_read(self, json2_config):
        conn = self._connection({("res.partner", "read"): True})
        ctrl = AccessController(json2_config, connection=conn)
        assert ctrl.is_model_enabled("res.partner") is True

    def test_is_model_enabled_without_read(self, json2_config):
        conn = self._connection({})
        ctrl = AccessController(json2_config, connection=conn)
        assert ctrl.is_model_enabled("res.partner") is False

    def test_check_operation_allowed(self, json2_config):
        conn = self._connection(
            {
                ("res.partner", "read"): True,
                ("res.partner", "write"): True,
                ("res.partner", "create"): False,
                ("res.partner", "unlink"): False,
            }
        )
        ctrl = AccessController(json2_config, connection=conn)

        allowed, msg = ctrl.check_operation_allowed("res.partner", "write")
        assert allowed is True
        assert msg is None

        allowed, msg = ctrl.check_operation_allowed("res.partner", "unlink")
        assert allowed is False
        assert "unlink" in msg

    def test_filter_enabled_models(self, json2_config):
        conn = self._connection(
            {
                ("res.partner", "read"): True,
                ("res.users", "read"): False,
            }
        )
        ctrl = AccessController(json2_config, connection=conn)

        result = ctrl.filter_enabled_models(["res.partner", "res.users", "sale.order"])

        assert "res.partner" in result
        assert "res.users" not in result
        assert "sale.order" not in result

    def test_permissions_cached_per_model(self, json2_config):
        conn = self._connection(
            {
                ("res.partner", "read"): True,
                ("res.partner", "write"): True,
                ("res.partner", "create"): True,
                ("res.partner", "unlink"): False,
            }
        )
        ctrl = AccessController(json2_config, connection=conn)

        ctrl.get_model_permissions("res.partner")
        ctrl.get_model_permissions("res.partner")  # cached

        # check_access_rights called exactly 4 times (not 8)
        assert conn.check_access_rights.call_count == 4

    def test_no_connection_filter_returns_all(self, json2_config):
        ctrl = AccessController(json2_config)

        result = ctrl.filter_enabled_models(["res.partner", "res.users", "sale.order"])

        assert result == ["res.partner", "res.users", "sale.order"]


if __name__ == "__main__":
    # Run integration tests when executed directly
    pytest.main([__file__, "-v", "-k", "Integration"])


class TestAccessCheckTimeouts:
    """Fail-fast when the customer's Odoo stops answering (2026-08-10 incident).

    A hanging Odoo used to cost 4x the RPC timeout per permissions fetch (one
    per CRUD probe) and the checks ran on the event loop, freezing the whole
    server. These tests pin the new behavior: stop after the first timeout,
    surface OdooTimeoutError, and run checks off the loop via validate_access.
    """

    @pytest.fixture
    def config(self):
        return OdooConfig(
            url="http://localhost:8069",
            api_key="test_key",
            database="testdb",
            api_version="json2",
        )

    def test_permissions_fetch_stops_after_first_timeout(self, config):
        conn = MagicMock()
        conn.check_access_rights.side_effect = OdooTimeoutError(
            "Operation timeout after 30 seconds"
        )
        ctrl = AccessController(config, connection=conn)

        with pytest.raises(OdooTimeoutError):
            ctrl.validate_model_access("res.partner", "read")

        # One probe, not one per CRUD operation
        assert conn.check_access_rights.call_count == 1

    def test_timeout_is_not_cached_as_denial(self, config):
        conn = MagicMock()
        conn.check_access_rights.side_effect = OdooTimeoutError("timeout")
        ctrl = AccessController(config, connection=conn)

        with pytest.raises(OdooTimeoutError):
            ctrl.validate_model_access("res.partner", "read")

        # Once the server answers again, permissions are fetched fresh
        conn.check_access_rights.side_effect = None
        conn.check_access_rights.return_value = True
        perms = ctrl.get_model_permissions("res.partner")
        assert perms.can_read is True

    def test_xmlrpc_check_access_rights_propagates_timeout(self):
        conn = MagicMock()
        conn.execute_kw.side_effect = OdooTimeoutError("Operation timeout after 30 seconds")

        with pytest.raises(OdooTimeoutError):
            OdooConnectionOrmMixin.check_access_rights(conn, "res.partner", "read")

    def test_xmlrpc_check_access_rights_other_errors_still_allow(self):
        # Non-timeout network errors keep the old behavior: assume allowed,
        # let the real operation fail with a clear error.
        conn = MagicMock()
        conn.execute_kw.side_effect = OdooConnectionError("Operation failed: boom")
        assert OdooConnectionOrmMixin.check_access_rights(conn, "res.partner", "read") is True

        conn.execute_kw.side_effect = OdooConnectionError("Model doesn't exist")
        assert OdooConnectionOrmMixin.check_access_rights(conn, "res.partner", "read") is False

    def test_json2_check_access_rights_propagates_timeout(self):
        conn = MagicMock()
        conn._call.side_effect = OdooTimeoutError("Request timeout after 30s")

        with pytest.raises(OdooTimeoutError):
            Json2OrmMixin.check_access_rights(conn, "res.partner", "read")

    async def test_validate_access_runs_off_the_event_loop(self, config):
        calls = []

        def record_thread(model, operation):
            calls.append(threading.current_thread())

        ctrl = MagicMock()
        ctrl.validate_model_access.side_effect = record_thread
        conn = MagicMock()

        await validate_access(conn, ctrl, "res.partner", "read")

        ctrl.validate_model_access.assert_called_once_with("res.partner", "read")
        assert calls[0] is not threading.main_thread()

    async def test_validate_access_propagates_denial(self, config):
        ctrl = MagicMock()
        ctrl.validate_model_access.side_effect = AccessControlError("denied")
        conn = MagicMock()

        with pytest.raises(AccessControlError):
            await validate_access(conn, ctrl, "res.partner", "write")
