"""Tests for OdooJSON2Connection ORM methods and live integration.

Unit tests use a mocked curl_cffi Session; integration tests require
a running Odoo 19 instance with ODOO_API_VERSION=json2.

Shared fixtures live in tests/helpers/json2_fixtures.py.
"""

# ruff: noqa: F811 -- fixture parameters intentionally shadow imported fixtures

import os

import pytest

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection
from tests.helpers.json2_fixtures import (  # noqa: F401
    _error_response,
    _ok_response,
    connected_json2,
    json2_config,
)

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
        mock_client.post.return_value = _ok_response([{"id": 1, "name": "Test"}])

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

        conn.read("res.partner", [1])

        body = mock_client.post.call_args[1]["json"]
        assert body["ids"] == [1]
        assert "fields" not in body

    def test_search_read(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response([{"id": 1, "name": "Test"}])

        result = conn.search_read("res.partner", [["active", "=", True]], fields=["name"], limit=5)

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
        conn.fields_get("res.partner", attributes=["string"])

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

    def test_check_access_rights_allowed(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response(True)

        result = conn.check_access_rights("res.partner", "read")

        assert result is True
        body = mock_client.post.call_args[1]["json"]
        assert body["operation"] == "read"
        assert body["raise_exception"] is False

    def test_check_access_rights_denied(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response(False)

        result = conn.check_access_rights("res.partner", "unlink")

        assert result is False

    def test_check_access_rights_returns_true_on_404(self, connected_json2):
        """404 means method not exposed — assume access allowed, let Odoo decide."""
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(404)

        result = conn.check_access_rights("some.model", "read")

        assert result is True

    def test_check_access_rights_returns_false_on_403(self, connected_json2):
        """403 means access denied."""
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(403)

        result = conn.check_access_rights("some.model", "read")

        assert result is False


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
        records = live_connection.search_read("res.partner", [], fields=["name", "email"], limit=3)
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
