"""Concurrency tests for the shared XML-RPC transport."""

import socketserver
import threading
import time
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

import pytest

from mcp_server_odoo.xmlrpc_transport import TimeoutTransport


class _ThreadedXMLRPCServer(socketserver.ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True


class _KeepAliveHandler(SimpleXMLRPCRequestHandler):
    protocol_version = "HTTP/1.1"


@pytest.fixture
def slow_xmlrpc_url():
    server = _ThreadedXMLRPCServer(
        ("127.0.0.1", 0), requestHandler=_KeepAliveHandler, logRequests=False
    )

    def slow_echo(value):
        time.sleep(0.2)
        return value

    server.register_function(slow_echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


def test_concurrent_calls_on_shared_transport_do_not_corrupt_socket(slow_xmlrpc_url):
    """ConnectionPool hands every proxy the same transport, so overlapping
    calls must take turns on its one socket instead of raising
    CannotSendRequest('Request-sent') / ResponseNotReady('Idle')."""
    transport = TimeoutTransport(5)
    proxies = [xmlrpc.client.ServerProxy(slow_xmlrpc_url, transport=transport) for _ in range(4)]

    with ThreadPoolExecutor(max_workers=len(proxies)) as pool:
        results = list(pool.map(lambda p: p[1].slow_echo(p[0]), enumerate(proxies)))

    assert results == [0, 1, 2, 3]
