"""execute_method asks a wizard's question through MCP's own round trip.

Drives a real MCPServer with the real 2.x client, in-process. A client that
speaks 2026-07-28 and can show a form gets an InputRequiredResult and answers
it; the retry completes the wizard. A legacy client gets the `followup` dict
it always got. See docs/adr/0004-interactive-elements-sdk-v2-and-mcp-apps.md.
"""

from unittest.mock import Mock

import pytest
from mcp.client import Client
from mcp.types import ElicitResult

from mcp_server_odoo.access_control import AccessController
from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.server import create_fastmcp_app
from mcp_server_odoo.tools import register_tools
from mcp_server_odoo.tools.wizards import WIZARD_REGISTRY

BACKORDER_ACTION = {
    "type": "ir.actions.act_window",
    "res_model": "stock.backorder.confirmation",
    "context": {"default_pick_ids": [(4, 5)], "default_show_transfers": False},
}
CALL = {"model": "stock.picking", "method": "button_validate", "ids": [5]}


def _app(connection):
    app = create_fastmcp_app()
    access = Mock(spec=AccessController)
    config = OdooConfig(url="http://localhost:8069", api_key="k", database="db")
    register_tools(app, connection, access, config)
    return app


@pytest.fixture
def connection():
    conn = Mock()
    conn.is_authenticated = True
    conn._base_url = "http://localhost:8069"
    # The first call returns the wizard; the retry re-runs the method and gets
    # the same wizard back; completing it answers True.
    conn.call_method.side_effect = [BACKORDER_ACTION, BACKORDER_ACTION, True]
    conn.create.return_value = 99
    return conn


@pytest.mark.asyncio
async def test_form_shown_and_answer_completes_wizard(connection):
    asked = []

    async def answer(context, params):
        asked.append(params)
        return ElicitResult(action="accept", content={"create_backorder": True})

    async with Client(_app(connection), elicitation_callback=answer) as client:
        result = await client.call_tool("execute_method", CALL)

    assert not result.is_error
    assert result.structured_content["result_kind"] == "completed"
    # The form is the wizard's own question and its own schema.
    (params,) = asked
    assert params.message == WIZARD_REGISTRY["stock.backorder.confirmation"].prompt
    assert "create_backorder" in params.requested_schema["properties"]
    # And the answer drove Odoo's wizard: created, then processed with a backorder.
    assert connection.create.call_args.args[0] == "stock.backorder.confirmation"
    assert connection.call_method.call_args.args[:2] == ("stock.backorder.confirmation", "process")


@pytest.mark.asyncio
async def test_declined_form_changes_nothing(connection):
    async def decline(context, params):
        return ElicitResult(action="decline")

    async with Client(_app(connection), elicitation_callback=decline) as client:
        result = await client.call_tool("execute_method", CALL)

    assert result.structured_content["success"] is False
    assert result.structured_content["result_kind"] == "declined"
    assert "declined" in result.structured_content["message"]
    connection.create.assert_not_called()
    # The retry did not even re-run the Odoo method.
    assert connection.call_method.call_count == 1


@pytest.mark.asyncio
async def test_explicit_decision_skips_the_form(connection):
    async def never(context, params):  # pragma: no cover - must not be reached
        raise AssertionError("no form should be shown when a decision is passed")

    # One call, no retry: the method returns the wizard, completing it answers True.
    connection.call_method.side_effect = [BACKORDER_ACTION, True]
    async with Client(_app(connection), elicitation_callback=never) as client:
        result = await client.call_tool(
            "execute_method", {**CALL, "decision": {"create_backorder": False}}
        )

    assert result.structured_content["result_kind"] == "completed"
    assert connection.call_method.call_args.args[:2] == (
        "stock.backorder.confirmation",
        "process_cancel_backorder",
    )


@pytest.mark.asyncio
async def test_legacy_client_gets_followup_dict(connection):
    async def never(context, params):  # pragma: no cover - must not be reached
        raise AssertionError("a legacy client cannot receive input_required")

    async with Client(_app(connection), elicitation_callback=never, mode="legacy") as client:
        result = await client.call_tool("execute_method", CALL)

    assert not result.is_error
    body = result.structured_content
    assert body["result_kind"] == "action"
    assert body["followup"]["wizard"] == "stock.backorder.confirmation"
    assert "create_backorder" in body["followup"]["decision_fields"]
    connection.create.assert_not_called()
