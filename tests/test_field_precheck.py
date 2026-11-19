"""Unknown field names are refused before Odoo is asked to read them.

Odoo answers `read` with an unknown field by raising ValueError (HTTP 500 on
JSON/2) and logging a traceback at ERROR level. The tools check an explicit
`fields` list against the cached `fields_get` first, so no such request is made.
"""

# ruff: noqa: F811 -- fixture parameters intentionally shadow imported fixtures

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.helpers.tool_fixtures import (  # noqa: F401
    handler,
    mock_access_controller,
    mock_app,
    mock_connection,
    valid_config,
)

SALE_ORDER_FIELDS = {
    "id": {"type": "integer"},
    "name": {"type": "char"},
    "partner_id": {"type": "many2one"},
    "amount_total": {"type": "monetary"},
    "prepayment_percent": {"type": "float"},
}


@pytest.fixture
def sale_order(mock_connection):
    mock_connection.fields_get.return_value = SALE_ORDER_FIELDS
    mock_connection.search_count.return_value = 1
    mock_connection.search.return_value = [4]
    mock_connection.read.return_value = [{"id": 4, "name": "S00004"}]
    return mock_connection


class TestSearchPrecheck:
    @pytest.mark.asyncio
    async def test_unknown_field_never_reaches_odoo(self, handler, sale_order, mock_app):
        with pytest.raises(ToolError) as err:
            await mock_app._tools["record_search"](
                model="sale.order", fields=["name", "require_payment"]
            )

        text = str(err.value)
        assert "sale.order has no field require_payment" in text
        assert "odoo://sale.order/fields" in text
        sale_order.search_count.assert_not_called()
        sale_order.search.assert_not_called()
        sale_order.read.assert_not_called()

    @pytest.mark.asyncio
    async def test_every_unknown_name_is_listed_with_close_matches(
        self, handler, sale_order, mock_app
    ):
        with pytest.raises(ToolError) as err:
            await mock_app._tools["record_search"](
                model="sale.order", fields=["partner", "name", "amount_totl", "require_payment"]
            )

        text = str(err.value)
        assert "partner (did you mean partner_id?)" in text
        assert "amount_totl (did you mean amount_total?)" in text
        assert "require_payment" in text
        assert "name," not in text

    @pytest.mark.asyncio
    async def test_known_fields_are_read_as_before(self, handler, sale_order, mock_app):
        await mock_app._tools["record_search"](
            model="sale.order", fields=["name", "partner_id", "display_name", "id"]
        )

        sale_order.read.assert_called_once_with(
            "sale.order", [4], ["name", "partner_id", "display_name", "id"]
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fields", [None, ["__all__"]])
    async def test_defaults_and_all_fields_are_not_checked(
        self, handler, sale_order, mock_app, fields
    ):
        sale_order.fields_get.return_value = SALE_ORDER_FIELDS

        await mock_app._tools["record_search"](model="sale.order", fields=fields)

        sale_order.read.assert_called_once()

    @pytest.mark.asyncio
    async def test_unreadable_field_list_leaves_the_call_to_odoo(
        self, handler, sale_order, mock_app
    ):
        sale_order.fields_get.side_effect = RuntimeError("fields_get failed")

        await mock_app._tools["record_search"](model="sale.order", fields=["require_payment"])

        sale_order.read.assert_called_once_with("sale.order", [4], ["require_payment"])

    @pytest.mark.asyncio
    async def test_fields_given_as_a_string_are_checked(self, handler, sale_order, mock_app):
        with pytest.raises(ToolError, match="sale.order has no field require_payment"):
            await mock_app._tools["record_search"](
                model="sale.order", fields="name, require_payment"
            )

        sale_order.read.assert_not_called()


class TestGetRecordPrecheck:
    @pytest.mark.asyncio
    async def test_unknown_field_never_reaches_odoo(self, handler, sale_order, mock_app):
        with pytest.raises(ToolError, match="did you mean partner_id"):
            await mock_app._tools["record_get"](model="sale.order", record_id=4, fields=["partner"])

        sale_order.read.assert_not_called()

    @pytest.mark.asyncio
    async def test_known_fields_are_read_as_before(self, handler, sale_order, mock_app):
        await mock_app._tools["record_get"](
            model="sale.order", record_id=4, fields=["name", "prepayment_percent"]
        )

        sale_order.read.assert_called_once_with("sale.order", [4], ["name", "prepayment_percent"])
