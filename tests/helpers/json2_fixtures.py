"""Shared fixtures and helpers for OdooJSON2Connection tests.

Imported by tests/test_json2_connection.py and tests/test_json2_orm.py.
"""

from unittest.mock import MagicMock

import pytest
from curl_cffi import requests as cffi_requests

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection


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
    """Return a connected+authenticated OdooJSON2Connection with a mocked curl_cffi session.

    Yields (conn, mock_client) so tests can configure mock_client.post / .get.
    """
    conn = OdooJSON2Connection(json2_config)
    conn._connected = True
    conn._authenticated = True
    conn._uid = 2
    conn._database = "testdb"

    mock_client = MagicMock(spec=cffi_requests.Session)
    # Default headers as a regular dict so .update() works
    mock_client.headers = {}
    conn._client = mock_client
    return conn, mock_client


def _ok_response(json_data):
    """Build a mock Response with status 200."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_data
    return resp


def _error_response(status_code, json_data=None, text="error"):
    """Build a mock Response with an error status."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp
