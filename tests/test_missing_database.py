"""Tests for permanent "database does not exist" classification.

A removed Odoo database is a permanent condition: retrying the same name
only makes Odoo log another "database does not exist". Both connection
backends raise ``OdooDatabaseNotFoundError`` so a retrying caller can stop
instead of re-dialing the missing database. Same intent as OdooTimeoutError.
"""

# ruff: noqa: F811 -- fixture parameters intentionally shadow imported fixtures

from unittest.mock import Mock, patch
from xmlrpc.client import Fault

import pytest

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.exceptions import (
    OdooConnectionError,
    OdooDatabaseNotFoundError,
    is_missing_database_message,
)
from mcp_server_odoo.odoo_connection import OdooConnection
from tests.helpers.json2_fixtures import (  # noqa: F401
    _error_response,
    connected_json2,
    json2_config,
)


class TestDetector:
    """The shared message detector."""

    @pytest.mark.parametrize(
        "text",
        [
            'FATAL:  database "acme" does not exist',
            'database "prod-1" does not exist',
            "psycopg2.OperationalError: database acme does not exist",
        ],
    )
    def test_matches_missing_database(self, text):
        assert is_missing_database_message(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "Access Denied",
            "Model 'res.partner' does not exist",
            "database connection reset",
        ],
    )
    def test_ignores_other_errors(self, text):
        assert is_missing_database_message(text) is False

    def test_is_connection_error_subclass(self):
        # Existing `except OdooConnectionError` handlers keep catching it.
        assert issubclass(OdooDatabaseNotFoundError, OdooConnectionError)


class TestJson2MissingDatabase:
    """JSON/2 backend (Odoo 19+)."""

    def test_call_raises_on_missing_database(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(
            500, text='FATAL:  database "testdb" does not exist'
        )
        with pytest.raises(OdooDatabaseNotFoundError, match="does not exist"):
            conn._call("res.partner", "search", domain=[])

    def test_check_access_rights_propagates_missing_database(self, connected_json2):
        """A removed database must propagate, not read as "no access"."""
        conn, mock_client = connected_json2
        mock_client.post.return_value = _error_response(
            500, text='database "testdb" does not exist'
        )
        with pytest.raises(OdooDatabaseNotFoundError):
            conn.check_access_rights("res.partner", "read")


class TestXmlrpcMissingDatabase:
    """XML-RPC backend (Odoo 14-18)."""

    @patch("urllib.request.urlopen")
    def test_authenticate_raises_and_skips_fallback(self, mock_urlopen):
        """A missing database stops auth instead of re-hitting it.

        The MCP endpoint 404s, so auth falls to standard XML-RPC. That call
        faults with the missing-database signature, which must surface as
        OdooDatabaseNotFoundError and must not trigger the password fallback
        (a second call against the same removed database).
        """
        import urllib.error

        config = OdooConfig(
            url="http://localhost:8069",
            api_key="test_api_key",
            username="admin",
            password="secret",
            database="acme",
        )
        conn = OdooConnection(config)
        conn._connected = True

        mock_urlopen.side_effect = urllib.error.HTTPError(None, 404, "Not Found", {}, None)
        mock_common = Mock()
        mock_common.authenticate.side_effect = Fault(3, 'database "acme" does not exist')
        conn._common_proxy = mock_common

        with pytest.raises(OdooDatabaseNotFoundError, match="acme"):
            conn.authenticate("acme")

        assert not conn.is_authenticated
        # One attempt only: no password fallback re-dial of the removed database.
        assert mock_common.authenticate.call_count == 1

    def test_execute_kw_raises_on_missing_database(self):
        config = OdooConfig(url="http://localhost:8069", api_key="k", database="acme")
        conn = OdooConnection(config)
        conn._connected = True
        conn._authenticated = True
        conn._uid = 2
        conn._database = "acme"
        conn._auth_method = "api_key"

        mock_object = Mock()
        mock_object.execute_kw.side_effect = Fault(2, 'database "acme" does not exist')
        conn._object_proxy = mock_object

        with pytest.raises(OdooDatabaseNotFoundError, match="acme"):
            conn.execute_kw("res.partner", "search", [[]], {})

    def test_check_access_rights_propagates_missing_database(self):
        """The generic "does not exist" handler must not swallow it as False."""
        config = OdooConfig(url="http://localhost:8069", api_key="k", database="acme")
        conn = OdooConnection(config)
        conn._connected = True
        conn._authenticated = True
        conn._uid = 2
        conn._database = "acme"
        conn._auth_method = "api_key"

        mock_object = Mock()
        mock_object.execute_kw.side_effect = Fault(2, 'database "acme" does not exist')
        conn._object_proxy = mock_object

        with pytest.raises(OdooDatabaseNotFoundError):
            conn.check_access_rights("res.partner", "read")
