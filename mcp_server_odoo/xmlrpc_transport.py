"""XML-RPC transports with socket timeouts.

stdlib ``xmlrpc.client.ServerProxy`` has no timeout parameter: an
unresponsive host (firewall dropping packets instead of refusing) holds
the connect for the kernel TCP timeout, ~130s on Linux. Any code path
that probes a user-supplied Odoo URL must use one of these transports.
"""

import threading
import xmlrpc.client
from urllib.parse import urlparse

# Per-socket-operation timeout for XML-RPC calls to Odoo servers.
DEFAULT_XMLRPC_TIMEOUT = 30


class TimeoutTransport(xmlrpc.client.Transport):
    """HTTP transport that applies a socket timeout, safe for concurrent use."""

    def __init__(self, timeout: float):
        super().__init__()
        self._timeout = timeout
        self._lock = threading.Lock()

    def make_connection(self, host):
        # The socket does not exist yet at this point; setting
        # ``conn.timeout`` makes http.client pass it to
        # socket.create_connection() and apply it to every recv.
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn

    def request(self, host, handler, request_body, verbose=False):
        # xmlrpc.client.Transport keeps one cached HTTPConnection and mutates
        # per-call state on itself, so it is not thread-safe. The connection
        # pool shares a single transport across its proxies, and tool calls run
        # in worker threads, so two threads can reach the same connection at
        # once. That leaves http.client's state machine mid-request and the
        # second thread fails at once with CannotSendRequest("Request-sent"),
        # which surfaced to the user as a false "Connection error". Serialize
        # here: a single socket is serial on the wire anyway, so no real
        # parallelism is lost, and stdlib rebuilds a broken connection on the
        # next call.
        with self._lock:
            return super().request(host, handler, request_body, verbose)


class TimeoutSafeTransport(TimeoutTransport, xmlrpc.client.SafeTransport):
    """HTTPS variant of TimeoutTransport."""


def transport_for_url(url: str, timeout: float) -> TimeoutTransport:
    """Pick the http/https transport matching the URL scheme."""
    if urlparse(url).scheme == "https":
        return TimeoutSafeTransport(timeout)
    return TimeoutTransport(timeout)
