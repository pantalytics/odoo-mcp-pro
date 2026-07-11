# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""Shared module-level constants and helpers for the tools package."""

from __future__ import annotations

import asyncio
import contextvars
import re
from typing import Any, Callable, TypeVar
from weakref import WeakKeyDictionary

from ..logging_config import get_logger

logger = get_logger("mcp_server_odoo.tools")

MAX_BULK_SIZE = 1000  # Maximum records per bulk operation
MAX_BINARY_SIZE_BYTES = 25 * 1024 * 1024  # set_binary_field upload cap
MAX_CONCURRENT_BINARY_UPLOADS = 3  # parallel set_binary_field calls; higher OOMs the server
_BINARY_UPLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_BINARY_UPLOADS)
_AVATAR_FIELD_RE = re.compile(r"^avatar_(128|256|512|1024|1920)$")

# ContextVar to pass the current user's sub from _get_user_context to the wrapper
_current_sub: contextvars.ContextVar[str] = contextvars.ContextVar("_current_sub", default="stdio")

T = TypeVar("T")

# Per-connection locks for off-loop RPC.
#
# Every blocking Odoo network call in the tool handlers runs via `run_blocking`
# below, which hands the call to a worker thread with `asyncio.to_thread` so a
# slow tenant no longer blocks the single event loop (and therefore every other
# tenant). The underlying transports are NOT safe for concurrent use of the
# SAME connection object, though: the XML-RPC backend reuses one
# `xmlrpc.client.ServerProxy` (a single persistent socket) and the JSON/2
# backend reuses one `curl_cffi.Session` plus an unlocked field cache. Two
# threads hitting the same connection at once would interleave request/response
# framing and corrupt results.
#
# We serialize per connection with an asyncio.Lock, NOT a global lock. The lock
# is keyed on the connection object, so concurrent calls for the *same* user
# (who share one cached connection) take turns, while different tenants hold
# different connections and run in parallel. This removes head-of-line blocking
# across tenants — the whole point of task #759 — without introducing a new
# cross-tenant bottleneck.
#
# A WeakKeyDictionary lets the lock disappear when the connection is evicted/
# garbage-collected, so nothing leaks as tenants come and go.
_connection_locks: "WeakKeyDictionary[Any, asyncio.Lock]" = WeakKeyDictionary()


def _lock_for(connection: Any) -> asyncio.Lock:
    """Return the asyncio.Lock guarding a single connection's transport.

    Lazily created and cached per connection object. asyncio.Lock binds to the
    running loop on first use, which is exactly the loop the tool handlers run
    on, so creating it here (inside an async handler) is safe.
    """
    lock = _connection_locks.get(connection)
    if lock is None:
        lock = asyncio.Lock()
        _connection_locks[connection] = lock
    return lock


async def run_blocking(connection: Any, func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking Odoo network call off the event loop.

    Wraps `asyncio.to_thread(func, *args, **kwargs)` and holds the
    per-connection lock for the duration, so the transport is never touched by
    two threads at once. `func` is typically a bound connection method
    (`connection.search`, `connection.read`, `connection.call_method`, ...).
    """
    async with _lock_for(connection):
        return await asyncio.to_thread(func, *args, **kwargs)
