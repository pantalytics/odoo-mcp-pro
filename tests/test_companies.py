"""Creates run in the company their values name (mcp_server_odoo/companies.py)."""

from unittest.mock import MagicMock, Mock

import pytest

from mcp_server_odoo.companies import bulk_company_context, company_context
from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.tools import OdooToolHandler

COMPANY_2 = {"allowed_company_ids": [2]}


class TestCompanyContext:
    def test_named_company_becomes_current(self):
        assert company_context({"name": "X", "company_id": 2}) == COMPANY_2

    def test_no_company_leaves_context_alone(self):
        assert company_context({"name": "X"}) is None

    def test_false_is_no_company(self):
        # False means "shared by all companies", not company 0.
        assert company_context({"name": "X", "company_id": False}) is None

    def test_bulk_one_company(self):
        rows = [{"name": "A", "company_id": 2}, {"name": "B", "company_id": 2}]
        assert bulk_company_context(rows) == COMPANY_2

    def test_bulk_no_company(self):
        assert bulk_company_context([{"name": "A"}, {"name": "B"}]) is None

    @pytest.mark.parametrize(
        "rows",
        [
            [{"company_id": 2}, {"company_id": 3}],
            [{"company_id": 2}, {"name": "no company"}],
        ],
    )
    def test_bulk_mixed_is_refused(self, rows):
        with pytest.raises(ValidationError, match="one records_create call per company"):
            bulk_company_context(rows)


@pytest.fixture
def connection():
    conn = Mock()
    conn.is_authenticated = True
    conn.fields_get.return_value = {
        "id": {},
        "name": {},
        "display_name": {},
        "company_id": {},
    }
    conn.read.return_value = [{"id": 7, "name": "X", "company_id": [2, "Two"]}]
    conn.create.return_value = 7
    conn.create_bulk.return_value = [7, 8]
    return conn


@pytest.fixture
def handler(connection):
    app = MagicMock()
    app.tool = MagicMock(return_value=lambda f: f)
    return OdooToolHandler(app=app, connection=connection, access_controller=Mock())


@pytest.mark.asyncio
async def test_record_create_runs_in_named_company(handler, connection):
    values = {"name": "X", "company_id": 2}
    result = await handler._handle_create_record_tool("account.move", values)

    connection.create.assert_called_once_with("account.move", values, context=COMPANY_2)
    assert result["record"]["company_id"] == [2, "Two"]


@pytest.mark.asyncio
async def test_record_create_without_company_sends_no_context(handler, connection):
    await handler._handle_create_record_tool("res.partner", {"name": "X"})

    connection.create.assert_called_once_with("res.partner", {"name": "X"})


@pytest.mark.asyncio
async def test_records_create_runs_in_named_company(handler, connection):
    rows = [{"name": "A", "company_id": 2}, {"name": "B", "company_id": 2}]
    await handler._handle_create_records_tool("res.partner", rows)

    connection.create_bulk.assert_called_once_with("res.partner", rows, context=COMPANY_2)


@pytest.mark.asyncio
async def test_records_create_mixed_companies_is_refused(handler, connection):
    rows = [{"name": "A", "company_id": 2}, {"name": "B", "company_id": 3}]
    with pytest.raises(ValidationError):
        await handler._handle_create_records_tool("res.partner", rows)
    connection.create_bulk.assert_not_called()


def test_json2_create_bulk_sends_context():
    from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection

    conn = MagicMock()
    conn._call.return_value = [1]
    conn._fields_cache = {}
    OdooJSON2Connection.create_bulk(conn, "res.partner", [{"name": "A"}], context=COMPANY_2)
    conn._call.assert_called_once_with(
        "res.partner", "create", vals_list=[{"name": "A"}], context=COMPANY_2
    )


def test_xmlrpc_create_bulk_sends_context():
    from mcp_server_odoo.odoo_connection import OdooConnection

    conn = MagicMock()
    conn.execute_kw.return_value = [1]
    OdooConnection.create_bulk(conn, "res.partner", [{"name": "A"}], context=COMPANY_2)
    conn.execute_kw.assert_called_once_with(
        "res.partner", "create", [[{"name": "A"}]], {"context": COMPANY_2}
    )


def test_xmlrpc_create_bulk_without_context_sends_none():
    from mcp_server_odoo.odoo_connection import OdooConnection

    conn = MagicMock()
    conn.execute_kw.return_value = [1]
    OdooConnection.create_bulk(conn, "res.partner", [{"name": "A"}])
    conn.execute_kw.assert_called_once_with("res.partner", "create", [[{"name": "A"}]], {})


@pytest.mark.parametrize("value", ["2", [2, "Two"], True, None])
def test_only_a_plain_company_id_picks_the_company(value):
    # A display pair or a string is not a create value Odoo takes; leave it to
    # Odoo to refuse rather than guess.
    assert company_context({"company_id": value}) is None
