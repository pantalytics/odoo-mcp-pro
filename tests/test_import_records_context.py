"""import_records hands the caller's context (lang among it) to Odoo's load().

Ticket #237 asked whether context={"lang": ...} is dropped on the way. These
tests follow it from the tool handler to the wire on both transports.
"""

from unittest.mock import MagicMock

import pytest

from mcp_server_odoo.odoo_connection import OdooConnection
from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection
from mcp_server_odoo.tools import OdooToolHandler

CONTEXT = {"lang": "nl_BE", "tracking_disable": True}


@pytest.fixture
def handler():
    conn = MagicMock()
    conn.is_authenticated = True
    conn.load_records = MagicMock(return_value={"ids": [7], "messages": []})
    app = MagicMock()
    app.tool = MagicMock(return_value=lambda f: f)
    return OdooToolHandler(app=app, connection=conn, access_controller=MagicMock())


@pytest.mark.asyncio
async def test_handler_passes_context_to_load_records(handler):
    await handler._handle_import_records_tool(
        "product.template", ["id", "name"], [["__import__.p1", "Stoel"]], CONTEXT
    )

    handler.connection.load_records.assert_called_once_with(
        "product.template", ["id", "name"], [["__import__.p1", "Stoel"]], CONTEXT
    )


def test_xmlrpc_load_sends_context_in_kwargs():
    conn = MagicMock(spec=OdooConnection)
    conn.execute_kw = MagicMock(return_value={"ids": [7], "messages": []})
    conn._performance_manager = MagicMock()

    OdooConnection.load_records(conn, "product.template", ["name"], [["Stoel"]], CONTEXT)

    conn.execute_kw.assert_called_once_with(
        "product.template", "load", [["name"], [["Stoel"]]], {"context": CONTEXT}
    )


def test_json2_load_sends_context_in_body():
    conn = MagicMock(spec=OdooJSON2Connection)
    conn._json2_url = "https://odoo.example/json/2"
    conn._fields_cache = {}
    response = MagicMock(status_code=200)
    response.json.return_value = {"ids": [7], "messages": []}
    conn._client = MagicMock()
    conn._client.post.return_value = response
    conn._call = lambda model, method, **kw: OdooJSON2Connection._call(conn, model, method, **kw)

    OdooJSON2Connection.load_records(conn, "product.template", ["name"], [["Stoel"]], CONTEXT)

    conn._client.post.assert_called_once_with(
        "https://odoo.example/json/2/product.template/load",
        json={"fields": ["name"], "data": [["Stoel"]], "context": CONTEXT},
    )
