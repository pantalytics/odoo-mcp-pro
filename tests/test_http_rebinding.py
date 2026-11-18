"""DNS rebinding in single-tenant HTTP mode (reported 2026-11-18).

HTTP mode has no credential of its own. Bound off loopback, a web page opened
inside the network could rebind its own name to the server and POST to /mcp
from the browser, calling every tool with the configured API key. The server
must:

  - refuse to start off loopback without ODOO_MCP_ALLOWED_HOSTS, before it
    connects to Odoo,
  - refuse a request whose Host is not listed (421) or whose Origin is a
    foreign site (403),
  - keep serving a listed Host, with or without the browser's Origin,
  - leave loopback as it was: the SDK's own loopback-only check.

These drive run_http itself and send real HTTP requests at the ASGI app it
hands to uvicorn, so the wiring is tested, not only the helper.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.error_handling import ConfigurationError
from mcp_server_odoo.server import ALLOWED_HOSTS_ENV, OdooMCPServer

CALL = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "ping", "arguments": {}},
}
HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
    "mcp-protocol-version": "2025-06-18",
}


def _server():
    config = OdooConfig(
        url="http://localhost:8069",
        api_key="test_api_key_12345",
        database="test_db",
        transport="streamable-http",
    )
    return OdooMCPServer(config)


def _app_from_run_http(host, port=8000):
    """Run run_http with Odoo and uvicorn stubbed; return the ASGI app it serves."""
    server = _server()

    @server.app.tool()
    def ping() -> str:
        return "pong"

    captured = {}

    def fake_config(app, **kwargs):
        captured["app"] = app
        return object()

    with (
        patch.object(server, "_ensure_connection"),
        patch.object(server, "_register_resources"),
        patch.object(server, "_register_tools"),
        patch("uvicorn.Config", side_effect=fake_config),
        patch("uvicorn.Server") as uv,
    ):
        uv.return_value.serve = AsyncMock()
        asyncio.run(server.run_http(host=host, port=port))
    return captured["app"]


def _post(app, host_header, origin=None):
    headers = dict(HEADERS, host=host_header)
    if origin:
        headers["origin"] = origin
    with TestClient(app, base_url=f"http://{host_header}") as client:
        response = client.post("/mcp", json=CALL, headers=headers)
    return response.status_code, "pong" in response.text


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::"])
def test_off_loopback_without_allowed_hosts_refuses_to_start(monkeypatch, host):
    monkeypatch.delenv(ALLOWED_HOSTS_ENV, raising=False)
    server = _server()
    with patch.object(server, "_ensure_connection") as connect:
        with pytest.raises(ConfigurationError, match=ALLOWED_HOSTS_ENV):
            asyncio.run(server.run_http(host=host, port=8000))
    connect.assert_not_called()  # refused before it ever reached Odoo


class TestOffLoopbackWithAllowedHosts:
    @pytest.fixture
    def app(self, monkeypatch):
        monkeypatch.setenv(ALLOWED_HOSTS_ENV, "mcp.lan, 10.0.0.5:9000")
        return _app_from_run_http("0.0.0.0", port=8000)

    @pytest.mark.parametrize(
        "host_header, origin",
        [
            ("evil.example:8000", "http://evil.example:8000"),  # the rebinding page
            ("evil.example:8000", None),
            ("10.0.0.5:8000", None),  # listed with another port
        ],
    )
    def test_foreign_host_refused(self, app, host_header, origin):
        assert _post(app, host_header, origin) == (421, False)

    def test_foreign_origin_on_a_listed_host_refused(self, app):
        assert _post(app, "mcp.lan:8000", "http://evil.example") == (403, False)

    @pytest.mark.parametrize(
        "host_header, origin",
        [
            ("mcp.lan:8000", None),  # an MCP client (no Origin)
            ("mcp.lan", None),
            ("mcp.lan:8000", "http://mcp.lan:8000"),  # a browser client on that host
            ("mcp.lan", "https://mcp.lan"),  # behind a TLS proxy on 443
            ("10.0.0.5:9000", None),
        ],
    )
    def test_listed_host_served(self, app, host_header, origin):
        assert _post(app, host_header, origin) == (200, True)


class TestLoopbackUnchanged:
    """Loopback needs no setting and keeps the SDK's own protection."""

    @pytest.fixture
    def app(self, monkeypatch):
        monkeypatch.delenv(ALLOWED_HOSTS_ENV, raising=False)
        return _app_from_run_http("localhost")

    @pytest.mark.parametrize("host_header", ["localhost:8000", "127.0.0.1:8000"])
    def test_local_client_served(self, app, host_header):
        assert _post(app, host_header) == (200, True)

    def test_rebinding_still_refused(self, app):
        assert _post(app, "evil.example:8000", "http://evil.example:8000") == (421, False)
