"""The connector-pro skill is found for questions about a Connector Pro sync."""

import pytest
from mcp.client import Client

from mcp_server_odoo.server import create_fastmcp_app


async def _find(question):
    async with Client(create_fastmcp_app()) as client:
        result = await client.call_tool("skill_find", {"question": question})
    assert not result.is_error
    return result.structured_content["result"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "why didn't my Shopify orders come in",
        "is it syncing with Picqer?",
        "what failed in the Exact Online sync",
        "Connector Pro staat op rood",
    ],
)
async def test_skill_find_returns_connector_pro(question):
    found = await _find(question)
    assert found.get("name") == "connector-pro"
    assert "pan.connector.sync" in found["content"]


@pytest.mark.asyncio
async def test_old_prepare_skill_is_gone():
    async with Client(create_fastmcp_app()) as client:
        result = await client.call_tool("skill_get", {"name": "connector-pro-prepare"})
    assert result.content[0].text == ""
