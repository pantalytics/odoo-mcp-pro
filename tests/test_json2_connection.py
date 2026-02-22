"""Tests for OdooJSON2Connection.

Unit tests use mocked httpx.Client; integration tests require
a running Odoo 19 instance with ODOO_API_VERSION=json2.
"""

import os
from unittest.mock import MagicMock, PropertyMock, patch

import httpx
import pytest

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_json2_connection import OdooConnectionError, OdooJSON2Connection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def json2_config():
    """Create a JSON/2 test configuration."""
    return OdooConfig(
        url="http://localhost:8069",
        api_key="test_api_key",
        database="testdb",
        api_version="json2",
    )


@pytest.fixture
def connected_json2(json2_config):
    """Return a connected+authenticated OdooJSON2Connection with a mocked httpx client.

    Yields (conn, mock_client) so tests can configure mock_client.post / .get.
    """
    conn = OdooJSON2Connection(json2_config)
    conn._connected = True
    conn._authenticated = True
    conn._uid = 2
    conn._database = "testdb"

    mock_client = MagicMock(spec=httpx.Client)
    # Default headers as a regular dict so .update() works
    mock_client.headers = {}
    conn._client = mock_client
    return conn, mock_client


def _ok_response(json_data):
    """Build a mock httpx.Response with status 200."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = json_data
    return resp


def _error_response(status_code, json_data=None, text="error"):
    """Build a mock httpx.Response with an error status."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


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
            OdooJSON2Connection(
                OdooConfig(url="ftp://localhost", api_key="k", api_version="json2")
            )

    def test_init_missing_hostname(self):
        with pytest.raises(OdooConnectionError, match="missing hostname"):
            OdooJSON2Connection(
                OdooConfig(url="http://", api_key="k", api_version="json2")
            )

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
        mock_client.post.return_value = _error_response(
            401, {"message": "Invalid token"}
        )
        with pytest.raises(OdooConnectionError, match="Authentication failed"):
            conn._call("res.partner", "search", domain=[])

    def test_call_403_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(
            403, {"message": "Access denied"}
        )
        with pytest.raises(OdooConnectionError, match="Access denied"):
            conn._call("res.partner", "search", domain=[])

    def test_call_404_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(
            404, {"message": "Model not found"}
        )
        with pytest.raises(OdooConnectionError, match="Not found"):
            conn._call("res.partner", "search", domain=[])

    def test_call_422_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(
            422, {"message": "Invalid domain"}
        )
        with pytest.raises(OdooConnectionError, match="Invalid request"):
            conn._call("res.partner", "search", domain=[])

    def test_call_500_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(500, text="Internal Server Error")
        with pytest.raises(OdooConnectionError, match="Server error"):
            conn._call("res.partner", "search", domain=[])

    def test_call_timeout_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.side_effect = httpx.TimeoutException("timed out")
        with pytest.raises(OdooConnectionError, match="Request timeout"):
            conn._call("res.partner", "search", domain=[])

    def test_call_connect_error_raises(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.side_effect = httpx.ConnectError("refused")
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
            with patch("httpx.Client") as MockClient:
                mock_instance = MagicMock()
                MockClient.return_value = mock_instance
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

        with patch.object(
            conn, "_fetch_version", side_effect=OdooConnectionError("no version")
        ):
            with patch("httpx.Client"):
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

    def test_disconnect_not_connected(self, json2_config, caplog):
        conn = OdooJSON2Connection(json2_config)
        conn.disconnect()
        assert "Not connected" in caplog.text

    def test_authenticate_not_connected(self, json2_config):
        conn = OdooJSON2Connection(json2_config)
        with pytest.raises(OdooConnectionError, match="Not connected"):
            conn.authenticate()

    def test_authenticate_no_api_key(self, json2_config):
        conn = OdooJSON2Connection(json2_config)
        conn._connected = True
        conn._client = MagicMock(spec=httpx.Client)
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
        with patch.object(OdooJSON2Connection, "connect") as mock_connect, patch.object(
            OdooJSON2Connection, "disconnect"
        ) as mock_disconnect:
            with OdooJSON2Connection(json2_config) as conn:
                mock_connect.assert_called_once()
                assert isinstance(conn, OdooJSON2Connection)

            mock_disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# ORM method tests
# ---------------------------------------------------------------------------


class TestOdooJSON2ORM:
    """Test ORM wrapper methods."""

    def test_search(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response([1, 2, 3])

        result = conn.search("res.partner", [["is_company", "=", True]], limit=10)

        assert result == [1, 2, 3]
        body = mock_client.post.call_args[1]["json"]
        assert body["domain"] == [["is_company", "=", True]]
        assert body["limit"] == 10

    def test_read_with_fields(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response(
            [{"id": 1, "name": "Test"}]
        )

        result = conn.read("res.partner", [1], fields=["name"])

        assert result == [{"id": 1, "name": "Test"}]
        body = mock_client.post.call_args[1]["json"]
        assert body["ids"] == [1]
        assert body["fields"] == ["name"]

    def test_read_without_fields(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response(
            [{"id": 1, "name": "Test", "email": "t@t.com"}]
        )

        result = conn.read("res.partner", [1])

        body = mock_client.post.call_args[1]["json"]
        assert body["ids"] == [1]
        assert "fields" not in body

    def test_search_read(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response(
            [{"id": 1, "name": "Test"}]
        )

        result = conn.search_read(
            "res.partner", [["active", "=", True]], fields=["name"], limit=5
        )

        assert result == [{"id": 1, "name": "Test"}]
        body = mock_client.post.call_args[1]["json"]
        assert body["domain"] == [["active", "=", True]]
        assert body["fields"] == ["name"]
        assert body["limit"] == 5

    def test_search_count(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response(42)

        result = conn.search_count("res.partner", [])

        assert result == 42

    def test_fields_get_cached(self, connected_json2):
        conn, mock_client = connected_json2
        fields_data = {"name": {"type": "char"}, "email": {"type": "char"}}
        mock_client.post.return_value = _ok_response(fields_data)

        # First call fetches from server
        result1 = conn.fields_get("res.partner")
        assert result1 == fields_data
        assert mock_client.post.call_count == 1

        # Second call uses cache
        result2 = conn.fields_get("res.partner")
        assert result2 == fields_data
        assert mock_client.post.call_count == 1  # No additional call

    def test_fields_get_with_attributes_not_cached(self, connected_json2):
        conn, mock_client = connected_json2
        fields_data = {"name": {"string": "Name"}}
        mock_client.post.return_value = _ok_response(fields_data)

        # Call with attributes — not cached
        result = conn.fields_get("res.partner", attributes=["string"])

        body = mock_client.post.call_args[1]["json"]
        assert body["attributes"] == ["string"]
        assert "res.partner" not in conn._fields_cache  # Not cached

    def test_create(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response([42])

        result = conn.create("res.partner", {"name": "New Partner"})

        assert result == 42
        body = mock_client.post.call_args[1]["json"]
        assert body["vals_list"] == [{"name": "New Partner"}]
        url = mock_client.post.call_args[0][0]
        assert url.endswith("/res.partner/create")

    def test_create_scalar_response(self, connected_json2):
        """Test create when server returns a scalar instead of list."""
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response(42)

        result = conn.create("res.partner", {"name": "New Partner"})
        assert result == 42

    def test_write(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response(True)

        result = conn.write("res.partner", [1, 2], {"name": "Updated"})

        assert result is True
        body = mock_client.post.call_args[1]["json"]
        assert body["ids"] == [1, 2]
        assert body["vals"] == {"name": "Updated"}

    def test_unlink(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response(True)

        result = conn.unlink("res.partner", [1])

        assert result is True
        body = mock_client.post.call_args[1]["json"]
        assert body["ids"] == [1]

    def test_get_server_version_not_connected(self, json2_config):
        conn = OdooJSON2Connection(json2_config)
        assert conn.get_server_version() is None

    def test_get_server_version_connected(self, connected_json2):
        conn, _ = connected_json2
        conn._version = {"server_version": "19.0"}
        assert conn.get_server_version() == {"server_version": "19.0"}


# ---------------------------------------------------------------------------
# Integration tests (require live Odoo 19 with json2)
# ---------------------------------------------------------------------------


@pytest.mark.json2_only
@pytest.mark.integration
class TestOdooJSON2Integration:
    """Integration tests against a live Odoo 19 instance."""

    @pytest.fixture
    def live_config(self):
        url = os.getenv("ODOO_URL")
        api_key = os.getenv("ODOO_API_KEY")
        db = os.getenv("ODOO_DB")
        if not url or not api_key:
            pytest.skip("ODOO_URL and ODOO_API_KEY required for integration tests")
        return OdooConfig(url=url, api_key=api_key, database=db, api_version="json2")

    @pytest.fixture
    def live_connection(self, live_config):
        conn = OdooJSON2Connection(live_config)
        conn.connect()
        conn.authenticate()
        yield conn
        conn.disconnect()

    def test_connect_and_authenticate(self, live_connection):
        assert live_connection.is_connected
        assert live_connection.is_authenticated
        assert live_connection.uid is not None
        assert live_connection.uid > 0

    def test_search_res_partner(self, live_connection):
        ids = live_connection.search("res.partner", [], limit=5)
        assert isinstance(ids, list)
        assert len(ids) <= 5
        assert all(isinstance(i, int) for i in ids)

    def test_search_read_res_partner(self, live_connection):
        records = live_connection.search_read(
            "res.partner", [], fields=["name", "email"], limit=3
        )
        assert isinstance(records, list)
        assert len(records) <= 3
        if records:
            assert "name" in records[0]
            assert "id" in records[0]

    def test_fields_get_res_partner(self, live_connection):
        fields = live_connection.fields_get("res.partner")
        assert isinstance(fields, dict)
        assert "name" in fields
        assert "email" in fields
        assert fields["name"]["type"] == "char"

    def test_search_count(self, live_connection):
        count = live_connection.search_count("res.partner", [])
        assert isinstance(count, int)
        assert count >= 0

    def test_read_records(self, live_connection):
        ids = live_connection.search("res.partner", [], limit=2)
        if ids:
            records = live_connection.read("res.partner", ids, fields=["name"])
            assert len(records) == len(ids)
            assert all("name" in r for r in records)

    def test_server_version(self, live_connection):
        version = live_connection.get_server_version()
        assert version is not None
        # Odoo 19 /web/version returns "version" (not "server_version")
        assert "server_version" in version or "version" in version
