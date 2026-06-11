"""Tests for OdooJSON2Connection.

Unit tests use a mocked curl_cffi Session; integration tests require
a running Odoo 19 instance with ODOO_API_VERSION=json2.

Shared fixtures live in tests/helpers/json2_fixtures.py.
ORM and integration tests live in tests/test_json2_orm.py.
"""

# ruff: noqa: F811 -- fixture parameters intentionally shadow imported fixtures

from unittest.mock import MagicMock, patch

import pytest
from curl_cffi import requests as cffi_requests
from curl_cffi.requests.errors import RequestsError

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_json2_connection import OdooConnectionError, OdooJSON2Connection
from tests.helpers.json2_fixtures import (  # noqa: F401
    _error_response,
    _ok_response,
    connected_json2,
    json2_config,
)

# ---------------------------------------------------------------------------
# Init tests
# ---------------------------------------------------------------------------


class TestOdooJSON2Init:
    """Test OdooJSON2Connection initialization."""

    def test_init_valid_config(self, json2_config):
        conn = OdooJSON2Connection(json2_config)
        assert conn._base_url == "http://localhost:8069"
        assert conn._json2_url == "http://localhost:8069/json/2"
        assert not conn.is_connected
        assert not conn.is_authenticated

    def test_init_invalid_scheme(self):
        with pytest.raises((OdooConnectionError, ValueError)):
            OdooJSON2Connection(OdooConfig(url="ftp://localhost", api_key="k", api_version="json2"))

    def test_init_missing_hostname(self):
        with pytest.raises(OdooConnectionError, match="missing hostname"):
            OdooJSON2Connection(OdooConfig(url="http://", api_key="k", api_version="json2"))

    def test_build_headers_with_database(self, connected_json2):
        conn, _ = connected_json2
        headers = conn._build_headers()
        assert headers["Authorization"] == "Bearer test_api_key"
        assert headers["X-Odoo-Database"] == "testdb"
        assert "application/json" in headers["Content-Type"]

    def test_build_headers_without_database(self, json2_config):
        conn = OdooJSON2Connection(json2_config)
        conn._database = None
        headers = conn._build_headers()
        assert "X-Odoo-Database" not in headers


# ---------------------------------------------------------------------------
# _call tests
# ---------------------------------------------------------------------------


class TestOdooJSON2Call:
    """Test the low-level _call method."""

    def test_call_not_connected_raises(self, json2_config):
        conn = OdooJSON2Connection(json2_config)
        with pytest.raises(OdooConnectionError, match="Not connected"):
            conn._call("res.partner", "search", domain=[])

    def test_call_200_returns_json(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response([1, 2, 3])

        result = conn._call("res.partner", "search", domain=[])

        assert result == [1, 2, 3]
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert call_url == "http://localhost:8069/json/2/res.partner/search"

    def test_call_401_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(401, {"message": "Invalid token"})
        with pytest.raises(OdooConnectionError, match="Authentication failed"):
            conn._call("res.partner", "search", domain=[])

    def test_call_403_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(403, {"message": "Access denied"})
        with pytest.raises(OdooConnectionError, match="Access denied"):
            conn._call("res.partner", "search", domain=[])

    def test_call_404_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(404, {"message": "Model not found"})
        with pytest.raises(OdooConnectionError, match="Not found"):
            conn._call("res.partner", "search", domain=[])

    def test_call_422_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(422, {"message": "Invalid domain"})
        with pytest.raises(OdooConnectionError, match="Invalid request"):
            conn._call("res.partner", "search", domain=[])

    def test_call_500_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(500, text="Internal Server Error")
        with pytest.raises(OdooConnectionError, match="Server error"):
            conn._call("res.partner", "search", domain=[])

    def test_call_timeout_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.side_effect = RequestsError("operation timed out")
        with pytest.raises(OdooConnectionError, match="Request timeout"):
            conn._call("res.partner", "search", domain=[])

    def test_call_connect_error_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.side_effect = RequestsError("could not connect to host")
        with pytest.raises(OdooConnectionError, match="Connection failed"):
            conn._call("res.partner", "search", domain=[])

    def test_call_filters_none_kwargs(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response([1])

        conn._call("res.partner", "search", domain=[], limit=5, offset=None)

        _, kwargs = mock_client.post.call_args
        body = kwargs["json"]
        assert "limit" in body
        assert "offset" not in body


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestOdooJSON2Lifecycle:
    """Test connect / disconnect / authenticate."""

    def test_connect_success(self, json2_config):
        conn = OdooJSON2Connection(json2_config)

        with patch.object(conn, "_fetch_version", return_value={"server_version": "19.0"}):
            with patch(
                "mcp_server_odoo.odoo_json2_connection.cffi_requests.Session"
            ) as mock_session_cls:
                mock_instance = MagicMock()
                mock_session_cls.return_value = mock_instance
                conn.connect()

        assert conn.is_connected
        assert conn._version == {"server_version": "19.0"}

    def test_connect_already_connected(self, json2_config, caplog):
        conn = OdooJSON2Connection(json2_config)
        conn._connected = True

        conn.connect()
        assert "Already connected" in caplog.text

    def test_connect_version_fails(self, json2_config):
        conn = OdooJSON2Connection(json2_config)

        with patch.object(conn, "_fetch_version", side_effect=OdooConnectionError("no version")):
            with patch("mcp_server_odoo.odoo_json2_connection.cffi_requests.Session"):
                with pytest.raises(OdooConnectionError, match="no version"):
                    conn.connect()

        assert not conn.is_connected

    def test_disconnect_clears_state(self, connected_json2):
        conn, mock_client = connected_json2
        conn.disconnect()

        assert not conn.is_connected
        assert not conn.is_authenticated
        assert conn.uid is None
        assert conn.database is None
        mock_client.close.assert_called_once()

    def test_disconnect_not_connected(self, json2_config):
        """Disconnect when not connected is a no-op."""
        conn = OdooJSON2Connection(json2_config)
        conn.disconnect()  # should not raise
        assert not conn.is_connected

    def test_authenticate_not_connected(self, json2_config):
        conn = OdooJSON2Connection(json2_config)
        with pytest.raises(OdooConnectionError, match="Not connected"):
            conn.authenticate()

    def test_authenticate_no_api_key(self, json2_config):
        conn = OdooJSON2Connection(json2_config)
        conn._connected = True
        conn._client = MagicMock(spec=cffi_requests.Session)
        conn._client.headers = {}
        conn.config.api_key = None

        with pytest.raises(OdooConnectionError, match="API key required"):
            conn.authenticate()

    def test_authenticate_success(self, connected_json2):
        conn, mock_client = connected_json2
        # Reset auth state
        conn._authenticated = False
        conn._uid = None

        mock_client.post.return_value = _ok_response({"uid": 42, "lang": "en_US"})

        conn.authenticate()

        assert conn.is_authenticated
        assert conn.uid == 42

    def test_authenticate_no_uid(self, connected_json2):
        conn, mock_client = connected_json2
        conn._authenticated = False
        conn._uid = None

        mock_client.post.return_value = _ok_response({"lang": "en_US"})

        with pytest.raises(OdooConnectionError, match="could not retrieve user ID"):
            conn.authenticate()

    def test_context_manager(self, json2_config):
        with (
            patch.object(OdooJSON2Connection, "connect") as mock_connect,
            patch.object(OdooJSON2Connection, "disconnect") as mock_disconnect,
        ):
            with OdooJSON2Connection(json2_config) as conn:
                mock_connect.assert_called_once()
                assert isinstance(conn, OdooJSON2Connection)

            # Python 3.10's GC may call __del__ within this scope, triggering
            # extra disconnect calls. The contract is that __exit__ calls it
            # at least once — assert that, not the exact count.
            mock_disconnect.assert_called()
