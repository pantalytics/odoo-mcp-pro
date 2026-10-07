"""SSRF guard on caller-supplied URLs (`safe_fetch`).

`set_binary_field` fetches whatever URL the caller passes. Without this guard
the server would read its own network (loopback services, RFC1918 hosts, the
169.254.169.254 cloud metadata address) and hand the bytes back as an Odoo
attachment. The guard must:

  - block literal loopback / private / link-local / reserved IPs (v4 + v6),
  - block 'localhost' and any '*.local' mDNS name,
  - block a public NAME that RESOLVES to an internal IP (DNS rebinding),
  - fail CLOSED on an unresolvable host,
  - allow a genuinely public IP / name,
  - re-check every redirect hop instead of following it blindly,
  - switch off only through ODOO_MCP_ALLOW_PRIVATE_FETCH (self-hosting on a LAN).

Pure functions plus httpx.MockTransport: no network, no Odoo.
"""

import socket

import httpx
import pytest

from mcp_server_odoo import safe_fetch
from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.safe_fetch import fetch_blocked, fetch_bytes, is_unreachable_host


def _fake_getaddrinfo(mapping):
    """socket.getaddrinfo stand-in: host -> ip, gaierror when unknown."""

    def _impl(host, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], 0))]

    return _impl


class TestLiteralIPs:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "10.0.0.5",
            "192.168.1.10",
            "172.16.0.1",
            "169.254.169.254",  # link-local / cloud metadata
            "0.0.0.0",
            "::1",
            "fe80::1",
            "fc00::1",
            "::ffff:169.254.169.254",  # IPv4-mapped IPv6 metadata addr
        ],
    )
    def test_internal_literal_ip_blocked(self, ip):
        assert is_unreachable_host(ip) is True

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
    def test_public_literal_ip_allowed(self, ip):
        assert is_unreachable_host(ip) is False


class TestNamedHosts:
    def test_localhost_blocked(self):
        assert is_unreachable_host("localhost") is True

    def test_mdns_local_suffix_blocked(self):
        assert is_unreachable_host("printer.local") is True
        assert is_unreachable_host("MyMac.LOCAL") is True

    def test_empty_host_blocked(self):
        assert is_unreachable_host("") is True
        assert is_unreachable_host(None) is True

    def test_public_name_resolving_public_allowed(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo({"cdn.example.com": "8.8.8.8"})
        )
        assert is_unreachable_host("cdn.example.com") is False

    def test_doc_range_is_reserved_and_blocked(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo({"doc.example.com": "203.0.113.7"})
        )
        assert is_unreachable_host("doc.example.com") is True

    def test_dns_rebinding_to_metadata_blocked(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo({"evil.example.com": "169.254.169.254"})
        )
        assert is_unreachable_host("evil.example.com") is True

    def test_dns_rebinding_to_private_blocked(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo({"rebind.example.com": "10.1.2.3"})
        )
        assert is_unreachable_host("rebind.example.com") is True

    def test_unresolvable_host_fails_closed(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({}))
        assert is_unreachable_host("nope.invalid") is True

    @pytest.mark.parametrize(
        "host",
        ["acme..example.com", "..example.com", "acme..example.org.", "x" * 64 + ".example.com"],
    )
    def test_malformed_hostname_fails_closed(self, host):
        """IDNA-rejected names make getaddrinfo raise UnicodeError, not gaierror.
        Not monkeypatched: the point is what the real resolver does with it."""
        assert is_unreachable_host(host) is True


class TestOptOut:
    def test_default_applies_the_check(self, monkeypatch):
        monkeypatch.delenv(safe_fetch.ALLOW_PRIVATE_FETCH_ENV, raising=False)
        assert fetch_blocked("127.0.0.1") is True
        assert fetch_blocked("8.8.8.8") is False

    def test_env_opt_out_allows_private(self, monkeypatch):
        monkeypatch.setenv(safe_fetch.ALLOW_PRIVATE_FETCH_ENV, "true")
        assert fetch_blocked("127.0.0.1") is False
        assert fetch_blocked("localhost") is False


# --- fetch_bytes: the redirect loop and the error surface ---

CANARY = b"internal-canary"
PUBLIC = b"public-bytes"


def _transport(monkeypatch):
    """A fake internet: public.example.com serves bytes and redirects, every
    loopback address serves the canary. DNS: public name -> public IP."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"public.example.com": "8.8.8.8"}))

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host in ("127.0.0.1", "localhost", "169.254.169.254"):
            return httpx.Response(200, content=CANARY)
        if path == "/file":
            return httpx.Response(200, content=PUBLIC)
        if path == "/to-loopback":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8069/secret"})
        if path == "/to-metadata":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/meta"})
        if path == "/to-public":
            return httpx.Response(302, headers={"location": "/file"})
        if path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"})
        if path == "/no-location":
            return httpx.Response(302)
        if path == "/big":
            return httpx.Response(200, content=b"x" * 200)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _fetch(url, transport, **kw):
    return await fetch_bytes(url, max_bytes=kw.pop("max_bytes", 1024), transport=transport, **kw)


@pytest.mark.asyncio
async def test_public_url_is_fetched(monkeypatch):
    t = _transport(monkeypatch)
    assert await _fetch("https://public.example.com/file", t) == PUBLIC


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8069/secret",
        "http://localhost:8069/secret",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
async def test_internal_source_refused_before_any_request(monkeypatch, url):
    t = _transport(monkeypatch)
    with pytest.raises(ValidationError, match="private or internal"):
        await _fetch(url, t)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/to-loopback", "/to-metadata"])
async def test_redirect_to_internal_refused(monkeypatch, path):
    """The reporter's case: a public URL that 302s to loopback."""
    t = _transport(monkeypatch)
    with pytest.raises(ValidationError, match="redirect points at"):
        await _fetch(f"https://public.example.com{path}", t)


@pytest.mark.asyncio
async def test_redirect_to_public_is_followed(monkeypatch):
    t = _transport(monkeypatch)
    assert await _fetch("https://public.example.com/to-public", t) == PUBLIC


@pytest.mark.asyncio
async def test_redirect_loop_stops(monkeypatch):
    t = _transport(monkeypatch)
    with pytest.raises(ValidationError, match="more than 5 times"):
        await _fetch("https://public.example.com/loop", t)


@pytest.mark.asyncio
async def test_redirect_without_location_refused(monkeypatch):
    t = _transport(monkeypatch)
    with pytest.raises(ValidationError, match="without a Location"):
        await _fetch("https://public.example.com/no-location", t)


@pytest.mark.asyncio
async def test_size_cap_applies(monkeypatch):
    t = _transport(monkeypatch)
    with pytest.raises(ValidationError, match="exceeds max size"):
        await _fetch("https://public.example.com/big", t, max_bytes=100)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://public.example.com/x", "not-a-url"])
async def test_non_http_scheme_refused(monkeypatch, url):
    t = _transport(monkeypatch)
    with pytest.raises(ValidationError, match="http\\(s\\) URL|missing a host"):
        await _fetch(url, t)


@pytest.mark.asyncio
async def test_http_error_becomes_validation_error(monkeypatch):
    t = _transport(monkeypatch)
    with pytest.raises(ValidationError, match="Failed to fetch"):
        await _fetch("https://public.example.com/missing", t)


@pytest.mark.asyncio
async def test_opt_out_lets_a_lan_fetch_through(monkeypatch):
    t = _transport(monkeypatch)
    monkeypatch.setenv(safe_fetch.ALLOW_PRIVATE_FETCH_ENV, "true")
    assert await _fetch("http://127.0.0.1:8069/secret", t) == CANARY
