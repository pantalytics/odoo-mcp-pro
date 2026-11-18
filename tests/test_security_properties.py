"""Property tests for the two guards that hold the network boundary.

The example lists in test_safe_fetch.py and test_http_rebinding.py confirm the
cases somebody thought of. Both holes reported on 2026-11-18 sat in the cases
nobody thought of (100.64.0.0/10; a Host header nobody listed). These tests
state the rule instead and let Hypothesis look for an input that breaks it.

The oracle for "not globally reachable" is the IANA special-purpose registry,
written out here, not Python's ipaddress: a test that asks the guard's own
library would only check the guard against itself. Only ranges that are
non-global as a WHOLE are listed (192.0.0.0/24 and 2001::/23 hold a few
global /32s, so they are left to the example tests).
"""

from ipaddress import IPv4Address, IPv6Address, ip_network

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.testclient import TestClient

from mcp_server_odoo.safe_fetch import is_unreachable_host
from mcp_server_odoo.server import ALLOWED_HOSTS_ENV, create_fastmcp_app, http_transport_security

# IANA IPv4/IPv6 special-purpose registries: "Globally Reachable = False" for the
# whole block, plus multicast (never a file server).
NOT_GLOBAL = [
    ip_network(n)
    for n in [
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/29",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::1/128",
        "::/128",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "100::/64",
        "2001:db8::/32",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    ]
]

# Blocks that are public in their entirety (APNIC/Level3/Hetzner/Cloudflare).
PUBLIC = [ip_network(n) for n in ["1.0.0.0/24", "8.0.0.0/9", "2a01:4f8::/32", "2606:4700::/32"]]


def _in(networks):
    """An address drawn from one of these networks, edges included."""

    def pick(net, offset):
        return str(net.network_address + offset % net.num_addresses)

    edges = st.sampled_from(networks).flatmap(
        lambda n: st.sampled_from([str(n.network_address), str(n.broadcast_address)])
    )
    inside = st.builds(pick, st.sampled_from(networks), st.integers(min_value=0, max_value=2**128))
    return st.one_of(edges, inside)


@settings(max_examples=1000, deadline=None)
@given(_in(NOT_GLOBAL))
def test_every_non_global_address_is_blocked(addr):
    assert is_unreachable_host(addr) is True


@settings(max_examples=500, deadline=None)
@given(_in(PUBLIC))
def test_public_addresses_pass(addr):
    assert is_unreachable_host(addr) is False


@settings(max_examples=1000, deadline=None)
@given(st.integers(min_value=0, max_value=2**32 - 1))
def test_ipv4_mapped_form_is_judged_as_the_ipv4_address(n):
    v4 = str(IPv4Address(n))
    assert is_unreachable_host(f"::ffff:{v4}") == is_unreachable_host(v4)


@settings(max_examples=300, deadline=None)
@given(st.integers(min_value=0, max_value=2**32 - 1))
def test_nat64_wrapping_never_unblocks(n):
    """Through NAT64 an address reaches its embedded IPv4: block what that blocks."""
    v4 = IPv4Address(n)
    if is_unreachable_host(str(v4)):
        assert is_unreachable_host(str(IPv6Address(int(IPv6Address("64:ff9b::")) | n))) is True


# --- The HTTP host check ---------------------------------------------------

LISTED = "mcp.lan"
PORT = 8000
_label = st.from_regex(r"[a-z0-9]([a-z0-9-]{0,20}[a-z0-9])?", fullmatch=True)
hostnames = st.lists(_label, min_size=1, max_size=4).map(".".join).filter(lambda h: h != LISTED)
ports = st.integers(min_value=1, max_value=65535)

_client = None


def _app_client():
    """One app for every example: the settings run_http builds for 0.0.0.0."""
    global _client
    if _client is None:
        mp = pytest.MonkeyPatch()
        mp.setenv(ALLOWED_HOSTS_ENV, LISTED)
        security = http_transport_security("0.0.0.0", PORT)
        mp.undo()
        app = create_fastmcp_app()

        @app.tool()
        def ping() -> str:
            return "pong"

        client = TestClient(
            app.streamable_http_app(
                host="0.0.0.0",
                transport_security=security,
                stateless_http=True,
                json_response=True,
            )
        )
        client.__enter__()
        _client = client
    return _client


def _status(host, origin=None):
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "mcp-protocol-version": "2025-06-18",
        "host": host,
    }
    if origin:
        headers["origin"] = origin
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "ping", "arguments": {}},
    }
    return _app_client().post("/mcp", json=body, headers=headers).status_code


@settings(max_examples=200, deadline=None)
@given(hostnames, st.one_of(st.none(), ports))
def test_any_unlisted_host_is_refused(name, port):
    host = name if port is None else f"{name}:{port}"
    assert _status(host) == 421


@settings(max_examples=200, deadline=None)
@given(ports.filter(lambda p: p != PORT))
def test_listed_name_on_another_port_is_refused(port):
    assert _status(f"{LISTED}:{port}") == 421


@settings(max_examples=200, deadline=None)
@given(st.sampled_from(["http", "https"]), hostnames, st.one_of(st.none(), ports))
def test_any_foreign_origin_is_refused(scheme, name, port):
    origin = f"{scheme}://{name}" + ("" if port is None else f":{port}")
    assert _status(f"{LISTED}:{PORT}", origin) == 403


@pytest.mark.parametrize("host", [LISTED, f"{LISTED}:{PORT}"])
@pytest.mark.parametrize("scheme", [None, "http", "https"])
def test_listed_host_and_its_own_origin_pass(host, scheme):
    origin = None if scheme is None else f"{scheme}://{host}"
    assert _status(host, origin) == 200
