"""Concurrency regression tests for the XML-RPC transports.

A single ``xmlrpc.client.Transport`` caches one HTTPConnection and mutates
per-call state on itself, so it is not thread-safe. The connection pool shares
one transport across its proxies and tool calls run in worker threads, so two
threads used to reach the same connection at once. The loser failed instantly
with ``http.client.CannotSendRequest("Request-sent")``, which surfaced as a
false ``OdooConnectionError``. These tests pin the serialization that stops it.
"""

import http.client
import threading
import time

import pytest

from mcp_server_odoo.xmlrpc_transport import (
    TimeoutSafeTransport,
    TimeoutTransport,
    transport_for_url,
)


class _ConcurrencyProbe:
    """Records the peak number of threads inside ``request`` at once."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def enter(self):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def leave(self):
        with self._guard:
            self.active -= 1


def _run_concurrently(target, count=8):
    threads = [threading.Thread(target=target) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


@pytest.mark.parametrize("cls", [TimeoutTransport, TimeoutSafeTransport])
def test_request_serializes_concurrent_calls(cls):
    """Only one thread runs the underlying request at a time."""
    transport = cls(timeout=5)
    probe = _ConcurrencyProbe()

    def fake_single_request(*args, **kwargs):
        probe.enter()
        try:
            time.sleep(0.01)  # widen the window a real overlap would use
            return "ok"
        finally:
            probe.leave()

    transport.single_request = fake_single_request

    def call():
        assert transport.request("host", "/handler", b"body") == "ok"

    _run_concurrently(call)

    assert probe.max_active == 1


def test_serialized_request_never_hits_request_sent():
    """A connection that rejects overlapping use is never entered twice.

    Emulates http.client's state machine: a second caller inside the request
    raises CannotSendRequest("Request-sent"). Serialization must keep every
    concurrent call from ever seeing that state.
    """
    transport = TimeoutTransport(timeout=5)
    in_flight = threading.Event()
    failures = []

    def fake_single_request(*args, **kwargs):
        if in_flight.is_set():
            raise http.client.CannotSendRequest("Request-sent")
        in_flight.set()
        try:
            time.sleep(0.01)
            return "ok"
        finally:
            in_flight.clear()

    transport.single_request = fake_single_request

    def call():
        try:
            transport.request("host", "/handler", b"body")
        except http.client.CannotSendRequest as exc:  # pragma: no cover
            failures.append(exc)

    _run_concurrently(call)

    assert failures == []


def test_transport_for_url_scheme_selection():
    assert isinstance(transport_for_url("https://odoo.example.com", 5), TimeoutSafeTransport)
    assert isinstance(transport_for_url("http://odoo.example.com", 5), TimeoutTransport)
