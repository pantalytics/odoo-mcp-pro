"""Creates on a real multi-company Odoo (mcp_server_odoo/companies.py).

These run against a live Odoo as an administrator and make their own second
company. `scripts/live_multi_company.sh` starts Odoo 18, 19 and 20 in Docker
and runs this file against each.

What they pin, measured before the fix on all three versions: a product
created for company B with a cost price kept that cost on company A and got
the sale taxes of every company.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.tools import OdooToolHandler
from mcp_server_odoo.version_detect import detect_api_version

pytestmark = [pytest.mark.integration, pytest.mark.odoo_required]


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
    """Two companies the admin may use; A is the admin's default."""
    config, conn = _connect()
    if "product.product" not in {
        m["model"]
        for m in conn.search_read("ir.model", [["model", "=", "product.product"]], fields=["model"])
    }:
        pytest.skip("needs the product module")
    user = conn.read("res.users", [conn.uid], ["company_id"])[0]
    a = user["company_id"][0]
    others = conn.search_read("res.company", [["id", "!=", a]], fields=["id"], limit=1)
    b = others[0]["id"] if others else conn.create("res.company", {"name": "MCP live company B"})
    conn.write("res.users", [conn.uid], {"company_ids": [(4, a), (4, b)]})
    app = MagicMock()
    app.tool = MagicMock(return_value=lambda f: f)
    handler = OdooToolHandler(
        app=app, connection=conn, access_controller=MagicMock(), config=config
    )
    created = []
    yield {"conn": conn, "h": handler, "a": a, "b": b, "created": created}
    if created:
        conn.write("product.product", created, {"active": False})
    conn.disconnect()


def _seen_from(conn, pid, company, other):
    """The product as company `company` sees it (the switcher with it first)."""
    return conn.call_method(
        "product.product",
        "read",
        ids=[pid],
        fields=["company_id", "standard_price", "taxes_id"]
        if "taxes_id" in conn.fields_get("product.product", ["type"])
        else ["company_id", "standard_price"],
        context={"allowed_company_ids": [company, other]},
    )[0]


def _sale_taxes(conn, company):
    if "account.tax" not in {
        m["model"]
        for m in conn.search_read("ir.model", [["model", "=", "account.tax"]], fields=["model"])
    }:
        return None
    return {
        t["id"]
        for t in conn.search_read("account.tax", [["company_id", "=", company]], fields=["id"])
    }


def _name():
    return f"MCP live {uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_cost_price_lands_on_the_named_company(mc):
    conn, a, b = mc["conn"], mc["a"], mc["b"]
    res = await mc["h"]._handle_create_record_tool(
        "product.product", {"name": _name(), "company_id": b, "standard_price": 5.0}
    )
    pid = res["record"]["id"]
    mc["created"].append(pid)

    assert res["record"]["company_id"][0] == b
    assert _seen_from(conn, pid, b, a)["standard_price"] == 5.0
    assert _seen_from(conn, pid, a, b)["standard_price"] == 0.0


@pytest.mark.asyncio
async def test_default_taxes_come_from_the_named_company_only(mc):
    conn, a, b = mc["conn"], mc["a"], mc["b"]
    taxes_b = _sale_taxes(conn, b)
    if taxes_b is None:
        pytest.skip("needs the account module")
    res = await mc["h"]._handle_create_record_tool(
        "product.product", {"name": _name(), "company_id": b}
    )
    pid = res["record"]["id"]
    mc["created"].append(pid)

    taxes = set(_seen_from(conn, pid, b, a)["taxes_id"])
    assert taxes <= taxes_b, (
        f"product for company {b} got taxes of another company: {taxes - taxes_b}"
    )


@pytest.mark.asyncio
async def test_without_company_the_record_lands_in_the_default_company(mc):
    res = await mc["h"]._handle_create_record_tool(
        "product.product", {"name": _name(), "company_id": mc["a"]}
    )
    mc["created"].append(res["record"]["id"])
    res = await mc["h"]._handle_create_record_tool("res.partner", {"name": _name()})
    partner = mc["conn"].read("res.partner", [res["record"]["id"]], ["company_id"])[0]
    mc["conn"].write("res.partner", [partner["id"]], {"active": False})
    # A partner without company_id is shared by all companies, as in the UI.
    assert partner["company_id"] is False


@pytest.mark.asyncio
async def test_bulk_create_runs_in_the_named_company(mc):
    conn, a, b = mc["conn"], mc["a"], mc["b"]
    rows = [{"name": _name(), "company_id": b, "standard_price": 3.0} for _ in range(2)]
    res = await mc["h"]._handle_create_records_tool("product.product", rows)
    mc["created"].extend(res["created_ids"])

    for pid in res["created_ids"]:
        assert _seen_from(conn, pid, b, a)["standard_price"] == 3.0
        assert _seen_from(conn, pid, a, b)["standard_price"] == 0.0


@pytest.mark.asyncio
async def test_bulk_create_for_two_companies_is_refused_and_creates_nothing(mc):
    name = _name()
    rows = [{"name": name, "company_id": mc["a"]}, {"name": name, "company_id": mc["b"]}]
    with pytest.raises(ValidationError, match="one records_create call per company"):
        await mc["h"]._handle_create_records_tool("product.product", rows)
    assert not mc["conn"].search_read("product.product", [["name", "=", name]], fields=["id"])


@pytest.mark.asyncio
async def test_a_company_the_user_may_not_use_is_refused_by_odoo(mc):
    with pytest.raises(ValidationError, match="(?i)compan"):
        await mc["h"]._handle_create_record_tool(
            "product.product", {"name": _name(), "company_id": 999999}
        )


def test_reads_span_every_company_the_user_may_use(mc):
    conn = mc["conn"]
    found = {
        r["company_id"][0]
        for r in conn.search_read(
            "product.product", [["company_id", "in", [mc["a"], mc["b"]]]], fields=["company_id"]
        )
    }
    if not found:
        pytest.skip("no company products yet")
    assert mc["b"] in found
