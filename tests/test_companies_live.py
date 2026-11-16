"""Creates and updates on a real multi-company Odoo (mcp_server_odoo/companies.py).

Run by `scripts/live_multi_company.sh`, which starts Odoo 18, 19 and 20 in
Docker, sets up the companies below and sets MCP_LIVE_MULTI_COMPANY=1. Without
that variable the file is skipped: it changes the database it runs against.

The database the script builds: the admin's default company A and a company
B, both with a chart of accounts, both in the admin's companies, and a
company C the admin is not in.

What they pin, measured before the fix on all three versions: a product
created for company B with a cost price kept that cost on company A and got
the sale taxes of every company.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

import pytest

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.tools import OdooToolHandler
from mcp_server_odoo.version_detect import detect_api_version

pytestmark = [
    pytest.mark.integration,
    pytest.mark.odoo_required,
    pytest.mark.skipif(
        os.getenv("MCP_LIVE_MULTI_COMPANY") != "1",
        reason="changes the database: run scripts/live_multi_company.sh",
    ),
]


def _connect():
    config = OdooConfig.from_env()
    if config.api_version == "auto":
        config.api_version = detect_api_version(config.url)[0]
    if config.api_version == "json2":
        from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection

        conn = OdooJSON2Connection(config)
    else:
        from mcp_server_odoo.odoo_connection import OdooConnection

        conn = OdooConnection(config)
    conn.connect()
    conn.authenticate()
    return config, conn


@pytest.fixture(scope="module")
def mc():
    config, conn = _connect()
    user = conn.read("res.users", [conn.uid], ["company_id", "company_ids"])[0]
    a = user["company_id"][0]
    others = [c for c in user["company_ids"] if c != a]
    outside = conn.search_read(
        "res.company", [["id", "not in", user["company_ids"]]], fields=["id"]
    )
    assert others and outside, "the script's companies are missing"
    app = MagicMock()
    app.tool = MagicMock(return_value=lambda f: f)
    handler = OdooToolHandler(
        app=app, connection=conn, access_controller=MagicMock(), config=config
    )
    yield {"conn": conn, "h": handler, "a": a, "b": others[0], "c": outside[0]["id"]}
    conn.disconnect()


def _cost(conn, pid, *companies):
    """The product's cost price as the first of `companies` sees it."""
    return conn.call_method(
        "product.product",
        "read",
        ids=[pid],
        fields=["standard_price"],
        context={"allowed_company_ids": list(companies)},
    )[0]["standard_price"]


def _name():
    return f"MCP live {uuid.uuid4().hex[:8]}"


async def _product(mc, **values):
    res = await mc["h"]._handle_create_record_tool("product.product", {"name": _name(), **values})
    return res["record"]


# -- create ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_price_lands_on_the_named_company(mc):
    conn, a, b = mc["conn"], mc["a"], mc["b"]
    record = await _product(mc, company_id=b, standard_price=5.0)

    assert record["company_id"][0] == b
    assert _cost(conn, record["id"], b) == 5.0
    # A does not see B's products alone; with both on and A first, this is
    # the value stored for A.
    assert _cost(conn, record["id"], a, b) == 0.0


@pytest.mark.asyncio
async def test_default_taxes_are_the_named_companys_sale_tax(mc):
    conn, b = mc["conn"], mc["b"]
    sale_tax = conn.read("res.company", [b], ["account_sale_tax_id"])[0]["account_sale_tax_id"]
    assert sale_tax, "company B has no default sale tax: the script loads a chart for it"
    record = await _product(mc, company_id=b)

    taxes = conn.call_method(
        "product.product",
        "read",
        ids=[record["id"]],
        fields=["taxes_id"],
        context={"allowed_company_ids": [b]},
    )[0]["taxes_id"]
    assert taxes == [sale_tax[0]]


@pytest.mark.asyncio
async def test_a_contact_without_company_stays_shared(mc):
    res = await mc["h"]._handle_create_record_tool("res.partner", {"name": _name()})
    partner = mc["conn"].read("res.partner", [res["record"]["id"]], ["company_id"])[0]
    assert partner["company_id"] is False


@pytest.mark.asyncio
async def test_an_invoice_without_company_goes_to_the_default_company(mc):
    partner = await mc["h"]._handle_create_record_tool("res.partner", {"name": _name()})
    res = await mc["h"]._handle_create_record_tool(
        "account.move", {"move_type": "out_invoice", "partner_id": partner["record"]["id"]}
    )
    assert res["record"]["company_id"][0] == mc["a"]


@pytest.mark.asyncio
async def test_bulk_create_runs_in_the_named_company(mc):
    conn, a, b = mc["conn"], mc["a"], mc["b"]
    rows = [{"name": _name(), "company_id": b, "standard_price": 3.0} for _ in range(2)]
    res = await mc["h"]._handle_create_records_tool("product.product", rows)

    for pid in res["created_ids"]:
        assert _cost(conn, pid, b) == 3.0
        assert _cost(conn, pid, a, b) == 0.0


@pytest.mark.asyncio
async def test_bulk_create_for_two_companies_is_refused_and_creates_nothing(mc):
    name = _name()
    rows = [{"name": name, "company_id": mc["a"]}, {"name": name, "company_id": mc["b"]}]
    with pytest.raises(ValidationError, match="one records_create call per company"):
        await mc["h"]._handle_create_records_tool("product.product", rows)
    assert not mc["conn"].search_read("product.product", [["name", "=", name]], fields=["id"])


@pytest.mark.asyncio
async def test_a_company_the_user_is_not_in_is_refused(mc):
    name = _name()
    with pytest.raises(ValidationError, match="(?i)unauthorized or invalid compan"):
        await mc["h"]._handle_create_record_tool(
            "product.product", {"name": name, "company_id": mc["c"]}
        )
    assert not mc["conn"].search_read("product.product", [["name", "=", name]], fields=["id"])


# -- update ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_updating_the_cost_price_changes_the_records_company(mc):
    conn, a, b = mc["conn"], mc["a"], mc["b"]
    record = await _product(mc, company_id=b, standard_price=5.0)
    await mc["h"]._handle_update_record_tool(
        "product.product", record["id"], {"standard_price": 7.0}
    )

    assert _cost(conn, record["id"], b) == 7.0
    assert _cost(conn, record["id"], a, b) == 0.0


@pytest.mark.asyncio
async def test_bulk_update_of_the_cost_price_changes_the_records_company(mc):
    conn, a, b = mc["conn"], mc["a"], mc["b"]
    ids = [(await _product(mc, company_id=b))["id"] for _ in range(2)]
    await mc["h"]._handle_update_records_tool("product.product", ids, {"standard_price": 4.0})

    for pid in ids:
        assert _cost(conn, pid, b) == 4.0
        assert _cost(conn, pid, a, b) == 0.0


@pytest.mark.asyncio
async def test_updating_a_templates_cost_price_changes_the_records_company(mc):
    # The usual way to set a cost: product.template.standard_price is computed,
    # and its inverse writes the variant's company-dependent cost.
    conn, a, b = mc["conn"], mc["a"], mc["b"]
    res = await mc["h"]._handle_create_record_tool(
        "product.template", {"name": _name(), "company_id": b}
    )
    template = res["record"]["id"]
    await mc["h"]._handle_update_record_tool("product.template", template, {"standard_price": 9.0})

    variant = conn.read("product.template", [template], ["product_variant_ids"])[0][
        "product_variant_ids"
    ][0]
    assert _cost(conn, variant, b) == 9.0
    assert _cost(conn, variant, a, b) == 0.0


# -- read --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_search_sees_every_company_of_the_user(mc):
    record = await _product(mc, company_id=mc["b"])
    found = mc["conn"].search_read("product.product", [["id", "=", record["id"]]], fields=["id"])
    assert found
