"""find_skill / get_skill tell the on_skill_call hook what they served.

The hosted layer uses the hook to send skill usage to analytics; self-hosted
leaves it unset. A broken hook must never break the tool.
"""

import pytest
from mcp.client import Client

from mcp_server_odoo.server import create_fastmcp_app


def _app(calls):
    return create_fastmcp_app(on_skill_call=lambda tool, skill: calls.append((tool, skill)))


@pytest.mark.asyncio
async def test_find_skill_reports_match():
    calls = []
    async with Client(_app(calls)) as client:
        result = await client.call_tool("find_skill", {"question": "maak een offerte"})
    assert not result.is_error
    assert calls == [("find_skill", "selling")]


@pytest.mark.asyncio
async def test_find_skill_reports_no_match():
    calls = []
    async with Client(_app(calls)) as client:
        await client.call_tool("find_skill", {"question": "xyzzy"})
    assert calls == [("find_skill", None)]


@pytest.mark.asyncio
async def test_get_skill_reports_known_and_unknown():
    calls = []
    async with Client(_app(calls)) as client:
        await client.call_tool("get_skill", {"name": "buying"})
        await client.call_tool("get_skill", {"name": "nope"})
    assert calls == [("get_skill", "buying"), ("get_skill", None)]


@pytest.mark.asyncio
async def test_failing_hook_does_not_break_tool():
    def boom(tool, skill):
        raise RuntimeError("tracker down")

    async with Client(create_fastmcp_app(on_skill_call=boom)) as client:
        result = await client.call_tool("get_skill", {"name": "buying"})
    assert not result.is_error
    assert "buying" in result.content[0].text
