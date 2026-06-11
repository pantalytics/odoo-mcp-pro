"""XML-RPC transports with socket timeouts.

stdlib ``xmlrpc.client.ServerProxy`` has no timeout parameter: an
unresponsive host (firewall dropping packets instead of refusing) holds
the connect for the kernel TCP timeout, ~130s on Linux. Any code path
that probes a user-supplied Odoo URL must use one of these transports.
"""

import xmlrpc.client
from urllib.parse import urlparse

# Per-socket-operation timeout for XML-RPC calls to Odoo servers.
DEFAULT_XMLRPC_TIMEOUT = 30


class TimeoutTransport(xmlrpc.client.Transport):
    """HTTP transport that applies a socket timeout."""

    def __init__(self, timeout: float):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        # The socket does not exist yet at this point; setting
        # ``conn.timeout`` makes http.client pass it to
        # socket.create_connection() and apply it to every recv.
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class TimeoutSafeTransport(TimeoutTransport, xmlrpc.client.SafeTransport):
    """HTTPS variant of TimeoutTransport."""


def transport_for_url(url: str, timeout: float) -> TimeoutTransport:
    """Pick the http/https transport matching the URL scheme."""
    if urlparse(url).scheme == "https":
        return TimeoutSafeTransport(timeout)
    return TimeoutTransport(timeout)
