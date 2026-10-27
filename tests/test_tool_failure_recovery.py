"""The failures agents hit most, and what they get back now.

Old tool names from before 4.0.0, guessed field names, a field list sent as a
string, and order/limit values Odoo or the tool used to refuse.
"""

# ruff: noqa: F811 -- fixture parameters intentionally shadow imported fixtures

from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_odoo.access_control import AccessController
from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.exceptions import OdooConnectionError, OdooExecutionError
from mcp_server_odoo.odoo_connection import OdooConnection
from mcp_server_odoo.server import create_fastmcp_app
from mcp_server_odoo.tool_aliases import LEGACY_TOOL_NAMES, accept_legacy_tool_names
from mcp_server_odoo.tools import OdooToolHandler
from mcp_server_odoo.tools.aggregate import normalize_order
from mcp_server_odoo.tools.field_hints import domain_field_names
from tests.helpers.tool_fixtures import (  # noqa: F401
    handler,
    mock_access_controller,
    mock_app,
    mock_connection,
    valid_config,
)

SOL_FIELDS = {
    "product_uom_id": {"type": "many2one"},
    "product_uom_qty": {"type": "float"},
    "price_subtotal": {"type": "monetary"},
    "order_id": {"type": "many2one"},
    "name": {"type": "text"},
}
INVALID = OdooExecutionError(
    "ValueError: Invalid field 'product_uom' on model 'sale.order.line'", error_type="ValueError"
)


def _real_app():
    app = create_fastmcp_app()
    connection = MagicMock(spec=OdooConnection)
    connection.is_authenticated = True
    connection.search_count.return_value = 1
    connection.search.return_value = [7]
    connection.read.return_value = [{"id": 7, "name": "Acme"}]
    config = OdooConfig(
        url="http://localhost:8069", api_key="k", database="db", default_limit=100, max_limit=500
    )
    OdooToolHandler(app, connection, MagicMock(spec=AccessController), config)
    return app, connection


class TestLegacyToolNames:
    @pytest.mark.asyncio
    async def test_old_name_runs_the_renamed_tool(self):
        app, connection = _real_app()

        result = await app.call_tool("search_records", {"model": "res.partner", "fields": ["name"]})

        assert not result.is_error
        connection.read.assert_called_once_with("res.partner", [7], ["name"])

    @pytest.mark.asyncio
    async def test_old_names_stay_out_of_the_tool_list(self):
        app, _ = _real_app()
        names = {tool.name for tool in await app.list_tools()}

        assert "record_search" in names
        assert not names & set(LEGACY_TOOL_NAMES)

    @pytest.mark.asyncio
    async def test_every_old_name_points_at_a_registered_tool(self):
        app, _ = _real_app()
        names = {tool.name for tool in await app.list_tools()}

        assert set(LEGACY_TOOL_NAMES.values()) <= names

    @pytest.mark.asyncio
    async def test_unknown_name_still_fails(self):
        app, _ = _real_app()

        with pytest.raises(ToolError, match="Unknown tool"):
            await app.call_tool("records_frobnicate", {})

    def test_installing_twice_adds_names_without_rewrapping(self):
        app = create_fastmcp_app()
        lookup = app._tool_manager.get_tool

        accept_legacy_tool_names(app, {"old_thing": "skill_find"})

        assert app._tool_manager.get_tool is lookup
        assert app._tool_manager.get_tool("old_thing").name == "skill_find"


class TestUnknownFieldHints:
    @pytest.mark.asyncio
    async def test_search_names_every_unknown_field(self, handler, mock_connection, mock_app):
        mock_connection.search_count.return_value = 1
        mock_connection.search.return_value = [1]
        mock_connection.read.side_effect = INVALID
        mock_connection.fields_get.return_value = SOL_FIELDS

        with pytest.raises(ToolError) as err:
            await mock_app._tools["record_search"](
                model="sale.order.line", fields=["product_uom", "qty_ordered", "name"]
            )

        text = str(err.value)
        assert "product_uom (did you mean product_uom_id" in text
        assert "qty_ordered" in text
        assert "name," not in text.split("Odoo said")[0]
        assert "fields_get" in text

    @pytest.mark.asyncio
    async def test_domain_field_is_checked_too(self, handler, mock_connection, mock_app):
        mock_connection.search_count.side_effect = INVALID
        mock_connection.fields_get.return_value = SOL_FIELDS

        with pytest.raises(ToolError, match="has no field product_uom "):
            await mock_app._tools["record_search"](
                model="sale.order.line", domain=[["product_uom.name", "=", "kg"]]
            )

    @pytest.mark.asyncio
    async def test_other_odoo_refusals_keep_odoos_message(self, handler, mock_connection, mock_app):
        mock_connection.search_count.side_effect = OdooExecutionError(
            "AccessError: You are not allowed to access 'Sales Order Line'",
            error_type="AccessError",
        )
        mock_connection.fields_get.return_value = SOL_FIELDS

        with pytest.raises(ToolError, match="not allowed to access") as err:
            await mock_app._tools["record_search"](model="sale.order.line", fields=["name"])
        assert "has no field" not in str(err.value)

    @pytest.mark.asyncio
    async def test_transport_failure_is_not_blamed_on_fields(
        self, handler, mock_connection, mock_app
    ):
        mock_connection.search_count.side_effect = OdooConnectionError("timed out")

        with pytest.raises(ToolError, match="Connection error"):
            await mock_app._tools["record_search"](model="sale.order.line", fields=["bogus"])
        mock_connection.fields_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_get(self, handler, mock_connection, mock_app):
        mock_connection.read.side_effect = INVALID
        mock_connection.fields_get.return_value = SOL_FIELDS

        with pytest.raises(ToolError, match="did you mean product_uom_id"):
            await mock_app._tools["record_get"](
                model="sale.order.line", record_id=3, fields=["product_uom"]
            )

    @pytest.mark.asyncio
    async def test_record_aggregate(self, handler, mock_connection, mock_app):
        mock_connection.read_group.side_effect = INVALID
        mock_connection.fields_get.return_value = SOL_FIELDS

        with pytest.raises(ToolError, match="did you mean product_uom_id"):
            await mock_app._tools["record_aggregate"](
                model="sale.order.line", groupby=["product_uom"], aggregates=["price_subtotal:sum"]
            )

    @pytest.mark.asyncio
    async def test_record_export(self, handler, mock_connection, mock_app):
        mock_connection.search.return_value = [1]
        mock_connection.call_method.side_effect = INVALID
        mock_connection.fields_get.return_value = SOL_FIELDS

        with pytest.raises(ToolError, match="did you mean product_uom_id"):
            await mock_app._tools["record_export"](
                model="sale.order.line", fields=["product_uom/name", "id", "order_id/name"]
            )

    def test_domain_field_names_skip_operators(self):
        domain = ["|", ["partner_id.name", "ilike", "a"], ("state", "=", "sale"), "!"]

        assert domain_field_names(domain) == ["partner_id", "state"]


class TestFieldsAsString:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fields, expected",
        [
            ('["name", "email"]', ["name", "email"]),
            ("['name', 'email']", ["name", "email"]),
            ("name, email", ["name", "email"]),
            ("name", ["name"]),
        ],
    )
    async def test_record_get_accepts_a_string(
        self, handler, mock_connection, mock_app, fields, expected
    ):
        mock_connection.read.return_value = [{"id": 1, "name": "Acme"}]

        await mock_app._tools["record_get"](model="res.partner", record_id=1, fields=fields)

        mock_connection.read.assert_called_once_with("res.partner", [1], expected)

    @pytest.mark.asyncio
    async def test_record_search_accepts_comma_separated(self, handler, mock_connection, mock_app):
        mock_connection.search_count.return_value = 1
        mock_connection.search.return_value = [1]
        mock_connection.read.return_value = [{"id": 1, "name": "Acme"}]

        await mock_app._tools["record_search"](model="res.partner", fields="name,email")

        mock_connection.read.assert_called_once_with("res.partner", [1], ["name", "email"])


class TestAggregateOrder:
    @pytest.mark.parametrize(
        "order, expected",
        [
            ("amount_total desc", "amount_total:sum desc"),
            ("date asc, amount_total desc", "date:month asc, amount_total:sum desc"),
            ("count desc", "__count desc"),
            ("__count desc", "__count desc"),
            ("partner_id", "partner_id"),
            ("unknown desc", "unknown desc"),
            (None, None),
        ],
    )
    def test_bare_names_become_their_spec(self, order, expected):
        groupby = ["partner_id", "date:month"]

        assert normalize_order(order, groupby, ["amount_total:sum"]) == expected

    def test_ambiguous_name_is_left_alone(self):
        aggregates = ["amount_total:sum", "amount_total:avg"]

        assert normalize_order("amount_total desc", [], aggregates) == "amount_total desc"

    @pytest.mark.asyncio
    async def test_tool_sends_the_rewritten_order(self, handler, mock_connection, mock_app):
        mock_connection.read_group.return_value = []

        await mock_app._tools["record_aggregate"](
            model="sale.order",
            groupby=["partner_id"],
            aggregates=["amount_total:sum"],
            order="amount_total desc",
        )

        assert mock_connection.read_group.call_args.kwargs["order"] == "amount_total:sum desc"
