"""Concurrency tests for the off-loop Odoo RPC fix (Odoo task #759).

The MCP server runs on a single asyncio event loop and the tool handlers are
`async def` but the Odoo network calls underneath are synchronous and blocking.
Before this fix those calls ran directly on the loop, so one tenant's slow Odoo
froze every other tenant for up to the socket timeout.

These tests prove the two halves of the fix:

1. Calls against *different* connections (different tenants) overlap: two slow
   calls finish in ~one call's wall time, not two. That only holds if the
   blocking call left the event loop (asyncio.to_thread).

2. Calls against the *same* connection are serialized by the per-connection
   lock, so two threads never touch one transport at once. We assert the slow
   sections never overlap.

The connection is mocked, so no real Odoo is needed.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest
from mcp.server import MCPServer

from mcp_server_odoo.access_control import AccessController
from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_connection import OdooConnection
from mcp_server_odoo.tools import OdooToolHandler

SLEEP = 0.3  # per-call blocking duration


def _make_app():
    """A fake FastMCP that captures registered tool functions by name."""
    app = MagicMock(spec=MCPServer)
    app._tools = {}

    def tool_decorator(**kwargs):
        def decorator(func):
            app._tools[func.__name__] = func
            return func

        return decorator

    app.tool = tool_decorator
    return app


def _make_config():
    return OdooConfig(
        url="http://localhost:8069",
        api_key="test_api_key",
        database="test_db",
        default_limit=100,
        max_limit=500,
    )


def _make_handler(connection):
    """Build a handler whose _get_user_context returns the given connection."""
    app = _make_app()
    access = MagicMock(spec=AccessController)
    handler = OdooToolHandler(app, connection, access, _make_config())
    return handler, app


def _slow_connection():
    """A mock connection whose search/read block for SLEEP seconds.

    Smart-default field selection is bypassed (search passes explicit fields)
    so the only blocking work is the search_count + search + read trio.
    """
    conn = MagicMock(spec=OdooConnection)
    conn.is_authenticated = True

    def slow_search_count(model, domain):
        time.sleep(SLEEP)
        return 1

    def slow_search(model, domain, **kwargs):
        return [1]

    def slow_read(model, ids, fields=None):
        return [{"id": 1, "name": "x"}]

    conn.search_count.side_effect = slow_search_count
    conn.search.side_effect = slow_search
    conn.read.side_effect = slow_read
    return conn


@pytest.mark.asyncio
async def test_two_tenants_do_not_block_each_other():
    """Two slow calls on *different* connections run concurrently.

    If the blocking RPC ran on the event loop, the two awaits would serialize
    and total wall time would be ~2*SLEEP. Off-loop, they overlap at ~1*SLEEP.
    """
    conn_a = _slow_connection()
    conn_b = _slow_connection()
    handler_a, app_a = _make_handler(conn_a)
    handler_b, app_b = _make_handler(conn_b)

    search_a = app_a._tools["search_records"]
    search_b = app_b._tools["search_records"]

    start = time.monotonic()
    await asyncio.gather(
        search_a(model="res.partner", domain=[], fields=["name"], limit=10),
        search_b(model="res.partner", domain=[], fields=["name"], limit=10),
    )
    elapsed = time.monotonic() - start

    # Generous bound: overlapping work is ~SLEEP; serialized would be ~2*SLEEP.
    assert elapsed < 1.8 * SLEEP, (
        f"two tenants took {elapsed:.2f}s (~{SLEEP}s expected if overlapping); "
        "the blocking call is likely still on the event loop"
    )


@pytest.mark.asyncio
async def test_event_loop_stays_responsive_during_blocking_call():
    """The loop keeps running while a tenant's blocking RPC is in flight.

    A 10ms heartbeat coroutine must tick many times during a SLEEP-long call.
    On the old code (blocking on the loop) it would not tick at all.
    """
    conn = _slow_connection()
    handler, app = _make_handler(conn)
    search = app._tools["search_records"]

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    hb = asyncio.create_task(heartbeat())
    await search(model="res.partner", domain=[], fields=["name"], limit=10)
    hb.cancel()

    # SLEEP / 0.01 = ~30 ticks; require a healthy fraction to prove liveness.
    assert ticks > 5, f"event loop only ticked {ticks} times during a blocking RPC"


@pytest.mark.asyncio
async def test_same_connection_calls_are_serialized():
    """Two concurrent calls on the SAME connection never run the transport
    concurrently.

    The XML-RPC ServerProxy / curl_cffi Session backing a single connection is
    not safe for concurrent thread use. The per-connection asyncio.Lock must
    serialize same-connection calls. We detect any overlap inside the blocking
    section via a non-reentrant counter.
    """
    conn = MagicMock(spec=OdooConnection)
    conn.is_authenticated = True

    in_flight = 0
    max_in_flight = 0
    guard = threading.Lock()

    def slow_search_count(model, domain):
        nonlocal in_flight, max_in_flight
        with guard:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(SLEEP)
        with guard:
            in_flight -= 1
        return 1

    conn.search_count.side_effect = slow_search_count
    conn.search.side_effect = lambda model, domain, **kw: [1]
    conn.read.side_effect = lambda model, ids, fields=None: [{"id": 1}]

    handler, app = _make_handler(conn)
    search = app._tools["search_records"]

    await asyncio.gather(
        search(model="res.partner", domain=[], fields=["name"], limit=10),
        search(model="res.partner", domain=[], fields=["name"], limit=10),
    )

    assert max_in_flight == 1, (
        f"same-connection transport ran {max_in_flight} calls at once; "
        "the per-connection lock failed to serialize them"
    )
