"""Fetching a user-supplied URL without reaching into the server's own network.

Every outbound HTTP call the server makes to a URL a caller chose (today: the
`source` of `set_binary_field`) goes through here. The guard refuses hosts the
server should never dial on a caller's behalf: loopback, RFC1918 private
ranges, link-local (the 169.254.169.254 cloud metadata address), reserved and
unspecified addresses, their IPv4-mapped IPv6 forms, `localhost` and `.local`
names. A name is RESOLVED and every address it resolves to is checked, so a
public name pointing at an internal IP (DNS rebinding) is refused too, and an
unresolvable name fails closed.

Redirects are never followed blindly: each hop is parsed and checked again
before it is fetched, because a benign public URL can 302 to an internal one.

Self-hosters whose file server lives on their own LAN can switch the guard
off with ODOO_MCP_ALLOW_PRIVATE_FETCH=true. There is no other fallback.

Known limit: the address is checked, then httpx resolves the name again to
connect. A resolver that answers differently on the second lookup can still
slip through; pinning the connection to the checked address is the next step
if that ever matters.
"""

from __future__ import annotations

import os
import socket
from ipaddress import ip_address
from typing import Optional
from urllib.parse import urlparse

import httpx

from .error_handling import ValidationError

ALLOW_PRIVATE_FETCH_ENV = "ODOO_MCP_ALLOW_PRIVATE_FETCH"


def is_unreachable_host(hostname: Optional[str]) -> bool:
    """True for a host this server must not fetch from on a caller's behalf.

    Pure classification, no env lookup: callers that want the self-hosting
    opt-out go through `fetch_blocked`.
    """
    host = (hostname or "").lower().strip()
    if not host:
        return True
    if host == "localhost" or host.endswith(".local"):
        return True

    def _internal(ip) -> bool:
        # Unwrap IPv4-mapped IPv6 (::ffff:169.254.169.254) before classifying.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        return (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
        )

    try:
        return _internal(ip_address(host))  # literal IP supplied directly
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # fail closed: unresolvable host is unreachable
    except (UnicodeError, ValueError):
        # A hostname getaddrinfo cannot even encode (an empty label, an
        # over-long one, anything IDNA rejects) resolves to nothing, so it
        # belongs with the unresolvable hosts. Answer, do not raise.
        return True
    for info in infos:
        try:
            if _internal(ip_address(info[4][0])):
                return True
        except ValueError:
            continue
    return False


def private_fetch_allowed() -> bool:
    """Whether the operator switched the guard off (self-hosting on a LAN)."""
    return os.getenv(ALLOW_PRIVATE_FETCH_ENV, "").strip().lower() in ("true", "1")


def fetch_blocked(hostname: Optional[str]) -> bool:
    """The gate every caller-supplied fetch applies, opt-out included."""
    if private_fetch_allowed():
        return False
    return is_unreachable_host(hostname)


def _checked_url(url: str, *, what: str) -> str:
    """Validate scheme, host and reachability of one URL (initial or redirect)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(f"{what} must be an http(s) URL, got scheme '{parsed.scheme}'")
    if not parsed.netloc:
        raise ValidationError(f"{what} is missing a host")
    if fetch_blocked(parsed.hostname):
        raise ValidationError(
            f"{what} points at '{parsed.hostname}', a private or internal address. "
            "This server only fetches public URLs. Self-hosting on a LAN? Set "
            f"{ALLOW_PRIVATE_FETCH_ENV}=true to allow it."
        )
    return url


async def fetch_bytes(
    source: str,
    *,
    max_bytes: int,
    timeout: float = 30.0,
    max_redirects: int = 5,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> bytes:
    """GET a caller-supplied URL and return its body, checking every hop.

    Raises ValidationError for a refused host, a bad scheme, too many
    redirects, a non-2xx answer, a transport error, or a body over max_bytes.
    `transport` exists for tests (httpx.MockTransport).
    """
    url = _checked_url(source, what="source")
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, transport=transport
    ) as client:
        try:
            for _hop in range(max_redirects + 1):
                async with client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise ValidationError("source redirected without a Location header")
                        url = _checked_url(str(resp.url.join(location)), what="source redirect")
                        continue
                    resp.raise_for_status()
                    chunks = []
                    total = 0
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValidationError(
                                f"Source exceeds max size of {max_bytes // (1024 * 1024)} MB"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
        except httpx.HTTPError as e:
            raise ValidationError(f"Failed to fetch source URL: {e}") from e
    raise ValidationError(f"source redirected more than {max_redirects} times")
