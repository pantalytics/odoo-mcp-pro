"""Tests for the record_aggregate tool and the read_group connection methods."""

# ruff: noqa: F811 -- fixture parameters intentionally shadow imported fixtures

from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_odoo.access_control import AccessControlError
from mcp_server_odoo.connection_protocol import OdooConnectionProtocol
from mcp_server_odoo.odoo_connection import OdooConnection
from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection
from tests.helpers.json2_fixtures import (  # noqa: F401
    _ok_response,
    connected_json2,
    json2_config,
)
from tests.helpers.tool_fixtures import (  # noqa: F401
    handler,
    mock_access_controller,
    mock_app,
    mock_connection,
    valid_config,
)


def _groups(n):
    return [
        {"product_id": [i, f"Product {i}"], "price_subtotal:sum": 10.0 * i, "__count": i}
        for i in range(1, n + 1)
    ]


class TestAggregateRecordsTool:
    @pytest.mark.asyncio
    async def test_grouped_totals(self, handler, mock_connection, mock_app):
        mock_connection.read_group.return_value = _groups(2)

        result = await mock_app._tools["record_aggregate"](
            model="account.move.line",
            groupby=["product_id", "date:month"],
            aggregates=["price_subtotal:sum", "quantity:sum"],
            domain=[["parent_state", "=", "posted"]],
            order="price_subtotal:sum desc",
        )

        args, kwargs = mock_connection.read_group.call_args
        assert args == (
            "account.move.line",
            [["parent_state", "=", "posted"]],
            ["product_id", "date:month"],
            ["price_subtotal:sum", "quantity:sum"],
        )
        assert kwargs == {"order": "price_subtotal:sum desc", "offset": 0, "limit": 101}
        assert result.groups == _groups(2)
        assert result.has_more is False
        assert result.model == "account.move.line"

    @pytest.mark.asyncio
    async def test_grand_total_without_groupby(self, handler, mock_connection, mock_app):
        """Weighted sum over all lines comes back as one compact row."""
        mock_connection.read_group.return_value = [{"balance:sum": 1234.5, "__count": 9000}]

        result = await mock_app._tools["record_aggregate"](
            model="account.move.line", aggregates=["balance:sum"]
        )

        assert mock_connection.read_group.call_args[0][2] == []
        assert result.groups == [{"balance:sum": 1234.5, "__count": 9000}]

    @pytest.mark.asyncio
    async def test_pagination_reports_has_more(self, handler, mock_connection, mock_app):
        mock_connection.read_group.return_value = _groups(4)

        result = await mock_app._tools["record_aggregate"](
            model="sale.order.line", groupby=["product_id"], limit=3, offset=6
        )

        kwargs = mock_connection.read_group.call_args[1]
        assert kwargs["limit"] == 4
        assert kwargs["offset"] == 6
        assert len(result.groups) == 3
        assert result.has_more is True
        assert (result.limit, result.offset) == (3, 6)

    @pytest.mark.asyncio
    async def test_domain_string_is_parsed(self, handler, mock_connection, mock_app):
        mock_connection.read_group.return_value = []

        await mock_app._tools["record_aggregate"](
            model="sale.order", groupby=["user_id"], domain="[['state', '=', 'sale']]"
        )

        assert mock_connection.read_group.call_args[0][1] == [["state", "=", "sale"]]

    @pytest.mark.asyncio
    async def test_count_aggregate_is_implicit(self, handler, mock_connection, mock_app):
        mock_connection.read_group.return_value = []

        await mock_app._tools["record_aggregate"](
            model="sale.order", groupby=["user_id"], aggregates=["__count", "amount_total:sum"]
        )

        assert mock_connection.read_group.call_args[0][3] == ["amount_total:sum"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({"aggregates": ["amount_total"]}, "Invalid aggregate"),
            ({"aggregates": ["line_ids:array_agg"]}, "Invalid aggregate"),
            ({"groupby": ["a", "b", "c", "d"]}, "at most 3"),
            ({"groupby": [""]}, "Invalid groupby"),
            ({"limit": 501}, "between 1 and 500"),
            ({"limit": 0}, "between 1 and 500"),
            ({"offset": -1}, "offset"),
        ],
    )
    async def test_rejects_unsafe_input(self, handler, mock_connection, mock_app, kwargs, message):
        with pytest.raises(ToolError, match=message):
            await mock_app._tools["record_aggregate"](model="sale.order", **kwargs)
        mock_connection.read_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_access_denied(self, handler, mock_access_controller, mock_app):
        mock_access_controller.validate_model_access.side_effect = AccessControlError("no read")

        with pytest.raises(ToolError, match="AccessError"):
            await mock_app._tools["record_aggregate"](model="account.move.line")


class TestXmlRpcReadGroup:
    @pytest.fixture
    def conn(self):
        conn = OdooConnection.__new__(OdooConnection)
        conn.execute_kw = MagicMock()
        return conn

    def test_aliases_aggregates_and_compacts_rows(self, conn):
        conn.execute_kw.return_value = [
            {
                "product_id": [7, "Desk"],
                "date:month": "October 2026",
                "quantity__sum": 12.0,
                "quantity__max": 5.0,
                "__count": 3,
                "__domain": [["product_id", "=", 7]],
                "__range": {"date:month": {"from": "2026-10-01", "to": "2026-11-01"}},
            }
        ]

        rows = conn.read_group(
            "account.move.line",
            [],
            ["product_id", "date:month"],
            ["quantity:sum", "quantity:max"],
            order="quantity:sum desc, date:month",
            limit=11,
        )

        conn.execute_kw.assert_called_once_with(
            "account.move.line",
            "read_group",
            [
                [],
                ["quantity__sum:sum(quantity)", "quantity__max:max(quantity)"],
                ["product_id", "date:month"],
            ],
            {"offset": 0, "lazy": False, "limit": 11, "orderby": "quantity__sum desc, date:month"},
        )
        assert rows == [
            {
                "product_id": [7, "Desk"],
                "date:month": ["2026-10-01", "October 2026"],
                "quantity:sum": 12.0,
                "quantity:max": 5.0,
                "__count": 3,
            }
        ]

    def test_label_only_dates_on_older_odoo(self, conn):
        conn.execute_kw.return_value = [{"date:month": "October 2026", "__count": 1}]

        rows = conn.read_group("sale.order", [], ["date:month"], [])

        assert rows == [{"date:month": "October 2026", "__count": 1}]


class TestJson2ReadGroup:
    def test_uses_formatted_read_group(self, connected_json2):
        conn, mock_client = connected_json2
        mock_client.post.return_value = _ok_response(
            [
                {
                    "product_id": [7, "Desk"],
                    "price_subtotal:sum": 99.5,
                    "__count": 2,
                    "__extra_domain": [["product_id", "=", 7]],
                }
            ]
        )

        rows = conn.read_group(
            "account.move.line",
            [["parent_state", "=", "posted"]],
            ["product_id"],
            ["price_subtotal:sum"],
            order="price_subtotal:sum desc",
            limit=6,
        )

        assert mock_client.post.call_args[0][0].endswith("/account.move.line/formatted_read_group")
        assert mock_client.post.call_args[1]["json"] == {
            "domain": [["parent_state", "=", "posted"]],
            "groupby": ["product_id"],
            "aggregates": ["price_subtotal:sum", "__count"],
            "offset": 0,
            "order": "price_subtotal:sum desc",
            "limit": 6,
        }
        assert rows == [{"product_id": [7, "Desk"], "price_subtotal:sum": 99.5, "__count": 2}]


def test_connections_satisfy_protocol():
    assert hasattr(OdooConnection, "read_group")
    assert hasattr(OdooJSON2Connection, "read_group")
    assert "read_group" in dir(OdooConnectionProtocol)
