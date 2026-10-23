"""Tests for the export_records tool."""

# ruff: noqa: F811 -- fixture parameters intentionally shadow imported fixtures

import csv
import io

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_odoo.access_control import AccessControlError
from mcp_server_odoo.exceptions import OdooExecutionError
from mcp_server_odoo.tools.export import rows_to_csv
from tests.helpers.tool_fixtures import (  # noqa: F401
    handler,
    mock_access_controller,
    mock_app,
    mock_connection,
    valid_config,
)

FIELDS = ["order_id/name", "product_id", "product_uom_qty"]


def _rows(ids):
    return [[f"S{i:05d}", f"Product {i}", float(i)] for i in ids]


def _parse(text):
    return list(csv.reader(io.StringIO(text)))


class TestExportRecordsTool:
    @pytest.mark.asyncio
    async def test_exports_csv_through_export_data(self, handler, mock_connection, mock_app):
        mock_connection.search_count.return_value = 2
        mock_connection.search.return_value = [7, 9]
        mock_connection.call_method.return_value = {"datas": _rows([7, 9])}

        result = await mock_app._tools["export_records"](
            model="sale.order.line",
            fields=FIELDS,
            domain=[["state", "=", "sale"]],
        )

        mock_connection.search.assert_called_once_with(
            "sale.order.line", [["state", "=", "sale"]], limit=500, offset=0, order="id"
        )
        mock_connection.call_method.assert_called_once_with(
            "sale.order.line", "export_data", ids=[7, 9], fields_to_export=FIELDS
        )
        assert _parse(result.csv) == [
            FIELDS,
            ["S00007", "Product 7", "7.0"],
            ["S00009", "Product 9", "9.0"],
        ]
        assert (result.records, result.rows, result.total) == (2, 2, 2)
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_pages_a_large_dataset(self, handler, mock_connection, mock_app):
        mock_connection.search_count.return_value = 20_000
        mock_connection.search.return_value = list(range(1001, 1501))
        mock_connection.call_method.return_value = {"datas": _rows(range(1001, 1501))}

        result = await mock_app._tools["export_records"](
            model="account.move.line", fields=FIELDS, offset=1000, order="date desc, id"
        )

        _, kwargs = mock_connection.search.call_args
        assert kwargs == {"limit": 500, "offset": 1000, "order": "date desc, id"}
        assert result.records == 500
        assert len(_parse(result.csv)) == 501
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_one2many_path_adds_rows(self, handler, mock_connection, mock_app):
        mock_connection.search_count.return_value = 1
        mock_connection.search.return_value = [3]
        mock_connection.call_method.return_value = {"datas": [["S00003", "Desk"], ["", "Chair"]]}

        result = await mock_app._tools["export_records"](
            model="sale.order", fields=["name", "order_line/product_id"]
        )

        assert (result.records, result.rows) == (1, 2)

    @pytest.mark.asyncio
    async def test_no_match_returns_header_only(self, handler, mock_connection, mock_app):
        mock_connection.search_count.return_value = 0
        mock_connection.search.return_value = []

        result = await mock_app._tools["export_records"](model="sale.order", fields=["name"])

        mock_connection.call_method.assert_not_called()
        assert result.csv == "name\n"
        assert result.has_more is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({"fields": []}, "at least one column"),
            ({"fields": [" "]}, "Invalid field path"),
            ({"fields": ["id", "name"]}, "external id"),
            ({"fields": ["partner_id/id"]}, "external id"),
            ({"fields": ["partner_id:id"]}, "external id"),
            ({"fields": ["tag_ids/id"]}, "external id"),
            ({"fields": ["name"], "limit": 501}, "between 1 and 500"),
            ({"fields": ["name"], "limit": 0}, "between 1 and 500"),
            ({"fields": ["name"], "offset": -1}, "offset"),
        ],
    )
    async def test_rejects_unsafe_input(self, handler, mock_connection, mock_app, kwargs, message):
        with pytest.raises(ToolError, match=message):
            await mock_app._tools["export_records"](model="sale.order", **kwargs)
        mock_connection.search.assert_not_called()
        mock_connection.call_method.assert_not_called()

    @pytest.mark.asyncio
    async def test_database_id_path_is_allowed(self, handler, mock_connection, mock_app):
        mock_connection.search_count.return_value = 1
        mock_connection.search.return_value = [5]
        mock_connection.call_method.return_value = {"datas": [["5", "12"]]}

        result = await mock_app._tools["export_records"](
            model="sale.order", fields=[".id", "partner_id/.id"]
        )

        assert _parse(result.csv)[1] == ["5", "12"]

    @pytest.mark.asyncio
    async def test_access_denied(self, handler, mock_access_controller, mock_app):
        mock_access_controller.validate_model_access.side_effect = AccessControlError("no read")

        with pytest.raises(ToolError, match="AccessError"):
            await mock_app._tools["export_records"](model="account.move.line", fields=["name"])

    @pytest.mark.asyncio
    async def test_odoo_export_permission_error_surfaces(self, handler, mock_connection, mock_app):
        mock_connection.search_count.return_value = 1
        mock_connection.search.return_value = [1]
        mock_connection.call_method.side_effect = OdooExecutionError(
            "UserError: You don't have the rights to export data.", error_type="UserError"
        )

        with pytest.raises(ToolError, match="rights to export"):
            await mock_app._tools["export_records"](model="res.partner", fields=["name"])


class TestRowsToCsv:
    def test_follows_odoo_csv_cell_rules(self):
        text = rows_to_csv(
            ["name", "note", "active", "amount", "formula"],
            [["Acme, Inc.", False, True, -12.5, "=SUM(A1)"], ['say "hi"', None, False, 0, "-x"]],
        )

        assert _parse(text) == [
            ["name", "note", "active", "amount", "formula"],
            ["Acme, Inc.", "", "True", "-12.5", "'=SUM(A1)"],
            ['say "hi"', "", "", "0", "'-x"],
        ]
