"""The one MCP App: register_payment carries a ui:// form (ADR 0004, phase 3).

Drives a real MCPServer with the real 2.x client, in-process. The resource is
served under the Apps MIME type, the tool binds it, and the fallbacks hold:
a client that negotiated Apps gets the followup dict the form reads (never an
input_required round trip it cannot see); a client without Apps but with form
elicitation gets that round trip; an explicit decision completes outright.
"""

import re
from unittest.mock import Mock

import pytest
from mcp.client import Client, advertise
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID
from mcp.types import ElicitResult

from mcp_server_odoo.access_control import AccessController
from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.server import create_fastmcp_app
from mcp_server_odoo.tools import register_tools
from mcp_server_odoo.tools.apps import REGISTER_PAYMENT_UI, register_payment_html

PAYMENT_ACTION = {
    "type": "ir.actions.act_window",
    "res_model": "account.payment.register",
    "context": {},
}
CALL = {"model": "account.move", "ids": [12]}
APPS_CLIENT = [advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})]


def _app(connection):
    app = create_fastmcp_app()
    config = OdooConfig(url="http://localhost:8069", api_key="k", database="db")
    register_tools(app, connection, Mock(spec=AccessController), config)
    return app


@pytest.fixture
def connection():
    conn = Mock()
    conn.is_authenticated = True
    conn._base_url = "http://localhost:8069"
    conn.call_method.side_effect = [PAYMENT_ACTION, PAYMENT_ACTION, {"payment": 1}]
    conn.create.return_value = 77
    return conn


@pytest.mark.asyncio
async def test_form_resource_is_served_and_bound(connection):
    async with Client(_app(connection), extensions=APPS_CLIENT) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == "register_payment")
        assert tool.meta["ui"]["resourceUri"] == REGISTER_PAYMENT_UI
        assert "ctx" not in tool.input_schema["properties"]
        listed = {r.uri: r.mime_type for r in (await client.list_resources()).resources}
        assert listed[REGISTER_PAYMENT_UI] == APP_MIME_TYPE
        read = (await client.read_resource(REGISTER_PAYMENT_UI)).contents[0]
        assert read.mime_type == APP_MIME_TYPE
        assert "ui/initialize" in read.text
        assert read.text == register_payment_html()


def test_form_needs_no_external_origin():
    """Bundled on purpose: the host's default CSP allows nothing else."""
    html = register_payment_html()
    assert not re.search(r"""(src|href)\s*=\s*["']https?://""", html)
    assert "fetch(" not in html and "XMLHttpRequest" not in html and "import(" not in html


@pytest.mark.asyncio
async def test_apps_client_gets_the_dict_the_form_reads(connection):
    async def never(context, params):  # pragma: no cover - must not be reached
        raise AssertionError("an Apps client must not get an input_required form")

    async with Client(
        _app(connection), extensions=APPS_CLIENT, elicitation_callback=never
    ) as client:
        result = await client.call_tool("register_payment", CALL)

    body = result.structured_content
    assert body["result_kind"] == "action"
    assert body["followup"]["wizard"] == "account.payment.register"
    assert set(body["followup"]["decision_fields"]) >= {"journal_id", "amount", "payment_date"}
    connection.create.assert_not_called()


@pytest.mark.asyncio
async def test_client_without_apps_falls_back_to_input_required(connection):
    async def accept_defaults(context, params):
        return ElicitResult(action="accept", content={})

    async with Client(_app(connection), elicitation_callback=accept_defaults) as client:
        result = await client.call_tool("register_payment", CALL)

    assert result.structured_content["result_kind"] == "completed"
    assert connection.create.call_args.args[0] == "account.payment.register"


@pytest.mark.asyncio
async def test_decision_from_the_form_completes(connection):
    connection.call_method.side_effect = [PAYMENT_ACTION, {"payment": 1}]
    async with Client(_app(connection), extensions=APPS_CLIENT) as client:
        result = await client.call_tool(
            "register_payment", {**CALL, "decision": {"journal_id": 7, "amount": 100.0}}
        )

    body = result.structured_content
    assert body["result_kind"] == "completed"
    assert body["method"] == "action_register_payment"
    _, vals = connection.create.call_args.args[:2]
    assert vals["journal_id"] == 7 and vals["amount"] == 100.0
