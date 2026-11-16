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

    connection.create.assert_called_once_with("res.partner", {"name": "X"}, context=None)


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


@pytest.mark.parametrize("value", [None, False, 0])
def test_no_company_leaves_the_context_alone(value):
    assert company_context({"company_id": value}) is None


@pytest.mark.parametrize("value", ["2", [2, "Two"], True, 2.0])
def test_a_company_id_that_is_no_id_is_refused(value):
    # A display pair copied from record_get, or a string: refuse it rather
    # than create in the wrong company without a word.
    with pytest.raises(ValidationError, match="company_id must be a company's id"):
        company_context({"company_id": value})


def test_bulk_explicit_false_with_a_company_is_refused():
    with pytest.raises(ValidationError):
        bulk_company_context([{"company_id": False}, {"company_id": 2}])


# -- updates: company-specific values go to the record's own company --------

FIELDS = {
    "company_id": {"type": "many2one"},
    "standard_price": {"type": "float", "company_dependent": True},
    "name": {"type": "char"},
}


def _update_connection(companies):
    conn = Mock()
    conn.is_authenticated = True
    conn.fields_get.return_value = FIELDS
    conn.read.side_effect = lambda model, ids, fields: [
        {"id": i, "company_id": [c, "C"] if c else False, "name": "X"}
        for i, c in zip(ids, companies, strict=True)
    ]
    return conn


def _handler_for(conn):
    app = MagicMock()
    app.tool = MagicMock(return_value=lambda f: f)
    return OdooToolHandler(app=app, connection=conn, access_controller=Mock())


@pytest.mark.asyncio
async def test_update_of_a_company_value_runs_in_the_records_company():
    conn = _update_connection([2])
    await _handler_for(conn)._handle_update_record_tool("product.product", 7, {"standard_price": 5})
    conn.write.assert_called_once_with(
        "product.product", [7], {"standard_price": 5}, context=COMPANY_2
    )


@pytest.mark.asyncio
async def test_update_of_plain_fields_sends_no_context():
    conn = _update_connection([2])
    await _handler_for(conn)._handle_update_record_tool("product.product", 7, {"name": "Y"})
    conn.write.assert_called_once_with("product.product", [7], {"name": "Y"}, context=None)


@pytest.mark.asyncio
async def test_update_of_a_shared_record_keeps_the_current_company():
    conn = _update_connection([None])
    await _handler_for(conn)._handle_update_record_tool("product.product", 7, {"standard_price": 5})
    conn.write.assert_called_once_with("product.product", [7], {"standard_price": 5}, context=None)


@pytest.mark.asyncio
async def test_bulk_update_of_company_values_across_companies_is_refused():
    conn = _update_connection([2, 3])
    with pytest.raises(ValidationError, match="different companies"):
        await _handler_for(conn)._handle_update_records_tool(
            "product.product", [7, 8], {"standard_price": 5}
        )
    conn.write.assert_not_called()
