"""Individual detection probes for Odoo server detection.

Each probe gathers one piece of evidence about a target URL (XML-RPC
version, /web/version, login page markers, server headers, JSON-RPC,
JSON/2 endpoint liveness, Cloudflare DNS). The probes are fired in
parallel and aggregated by `mcp_server_odoo.detection`.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import xmlrpc.client
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from curl_cffi import requests as cffi_requests

PROBE_TIMEOUT_SECONDS = 5
_MAJOR_VERSION_RE = re.compile(r"(\d+)")
_IMPERSONATE = "chrome"

# Cloudflare's published IPv4 ranges (well-known, stable; refresh occasionally
# from https://www.cloudflare.com/ips-v4 if you want to keep this current).
_CLOUDFLARE_V4 = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
]
_CLOUDFLARE_NETS = [ipaddress.ip_network(c) for c in _CLOUDFLARE_V4]


@dataclass
class ProbeResult:
    name: str
    ok: bool
    latency_ms: int
    status_code: Optional[int] = None
    summary: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "summary": self.summary,
        }
        if self.status_code is not None:
            d["status_code"] = self.status_code
        if self.error is not None:
            d["error"] = self.error
        return d


def _parse_major(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if not value:
        return None
    m = _MAJOR_VERSION_RE.search(str(value))
    return int(m.group(1)) if m else None


def _timed(fn):
    """Wrap a sync probe so it records its own latency + catches exceptions."""

    def wrapper(*args, **kwargs) -> ProbeResult:
        name = fn.__name__.replace("_probe_", "")
        t0 = perf_counter()
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # safety net: probes must never crash the runner
            return ProbeResult(
                name=name,
                ok=False,
                latency_ms=int((perf_counter() - t0) * 1000),
                error=f"{type(e).__name__}: {e}",
            )

    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


@_timed
def _probe_xmlrpc_version(url: str, timeout: int) -> ProbeResult:
    """P1: XML-RPC common.version(). Uses stdlib http.client TLS fingerprint."""
    t0 = perf_counter()
    proxy = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)
    info = proxy.version()  # may raise on TLS reject / 4xx / timeout
    sv = info.get("server_version", "")
    svi = info.get("server_version_info") or []
    major = _parse_major(svi[0] if svi else None) or _parse_major(sv)
    return ProbeResult(
        name="xmlrpc_version",
        ok=bool(major),
        latency_ms=int((perf_counter() - t0) * 1000),
        summary={"server_version": sv, "major": major, "raw_info": svi[:6]},
    )


@_timed
def _probe_web_version(url: str, timeout: int) -> ProbeResult:
    """P2: GET /web/version with Chrome TLS — works through Cloudflare."""
    t0 = perf_counter()
    r = cffi_requests.get(
        f"{url}/web/version",
        impersonate=_IMPERSONATE,
        timeout=timeout,
        allow_redirects=True,
    )
    summary: Dict[str, Any] = {}
    if r.status_code == 200:
        try:
            data = r.json()
            sv = data.get("version") or data.get("server_version", "")
            svi = data.get("version_info") or data.get("server_version_info") or []
            summary = {
                "server_version": sv,
                "major": _parse_major(svi[0] if svi else None) or _parse_major(sv),
                "raw_info": svi[:6],
            }
        except Exception:
            summary = {"body_snippet": r.text[:200]}
    return ProbeResult(
        name="web_version",
        ok=r.status_code == 200 and bool(summary.get("major")),
        latency_ms=int((perf_counter() - t0) * 1000),
        status_code=r.status_code,
        summary=summary,
    )


@_timed
def _probe_web_health(url: str, timeout: int) -> ProbeResult:
    """GET /web/health — Odoo 17+ returns 200 with 'pass' in the body."""
    t0 = perf_counter()
    r = cffi_requests.get(
        f"{url}/web/health",
        impersonate=_IMPERSONATE,
        timeout=timeout,
        allow_redirects=True,
    )
    text = (r.text or "")[:300]
    is_odoo_health = r.status_code == 200 and "pass" in text
    return ProbeResult(
        name="web_health",
        ok=is_odoo_health,
        latency_ms=int((perf_counter() - t0) * 1000),
        status_code=r.status_code,
        summary={"has_pass_marker": is_odoo_health, "body_snippet": text[:120]},
    )


@_timed
def _probe_web_login(url: str, timeout: int) -> ProbeResult:
    """GET /web/login — looks for Odoo HTML markers + the db_monodb cookie."""
    t0 = perf_counter()
    r = cffi_requests.get(
        f"{url}/web/login",
        impersonate=_IMPERSONATE,
        timeout=timeout,
        allow_redirects=True,
    )
    text_low = (r.text or "").lower()
    has_o_login = "o_login" in text_low or "o_database_list" in text_low
    has_odoo_word = "odoo" in text_low
    cookies = r.cookies if hasattr(r, "cookies") else {}
    db_monodb = None
    try:
        db_monodb = cookies.get("db_monodb") if cookies else None  # type: ignore[union-attr]
    except Exception:
        pass
    return ProbeResult(
        name="web_login",
        ok=r.status_code == 200 and (has_o_login or has_odoo_word),
        latency_ms=int((perf_counter() - t0) * 1000),
        status_code=r.status_code,
        summary={
            "has_o_login_class": has_o_login,
            "has_odoo_word": has_odoo_word,
            "db_monodb_cookie": db_monodb,
        },
    )


@_timed
def _probe_root_headers(url: str, timeout: int) -> ProbeResult:
    """HEAD / — read the `server:` header. 'Odoo.sh' / 'nginx' / 'cloudflare'."""
    t0 = perf_counter()
    r = cffi_requests.request(
        "HEAD",
        url,
        impersonate=_IMPERSONATE,
        timeout=timeout,
        allow_redirects=True,
    )
    server = ""
    try:
        server = (r.headers.get("server") or "").strip()
    except Exception:
        pass
    return ProbeResult(
        name="root_headers",
        ok=bool(server),
        latency_ms=int((perf_counter() - t0) * 1000),
        status_code=r.status_code,
        summary={"server": server, "behind_cloudflare": "cloudflare" in server.lower()},
    )


@_timed
def _probe_jsonrpc_version(url: str, timeout: int) -> ProbeResult:
    """POST /jsonrpc common.version — Odoo's pre-19 JSON-RPC equivalent of XML-RPC."""
    t0 = perf_counter()
    body = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {"service": "common", "method": "version", "args": []},
    }
    r = cffi_requests.post(
        f"{url}/jsonrpc",
        json=body,
        impersonate=_IMPERSONATE,
        timeout=timeout,
        allow_redirects=True,
    )
    summary: Dict[str, Any] = {}
    if r.status_code == 200:
        try:
            data = r.json()
            result = data.get("result") or {}
            sv = result.get("server_version", "")
            svi = result.get("server_version_info") or []
            summary = {
                "server_version": sv,
                "major": _parse_major(svi[0] if svi else None) or _parse_major(sv),
                "raw_info": svi[:6],
            }
        except Exception:
            summary = {"body_snippet": r.text[:200]}
    return ProbeResult(
        name="jsonrpc_version",
        ok=r.status_code == 200 and bool(summary.get("major")),
        latency_ms=int((perf_counter() - t0) * 1000),
        status_code=r.status_code,
        summary=summary,
    )


@_timed
def _probe_json2_unauth(url: str, timeout: int) -> ProbeResult:
    """POST /json/2/res.users/context_get without auth.

    Odoo 19+ replies 401 with `name: werkzeug.exceptions.Unauthorized` and
    message "Invalid apikey". That's a strong "JSON/2 endpoint is live" signal,
    so the server is v19+. Pre-v19 returns 404.
    """
    t0 = perf_counter()
    r = cffi_requests.post(
        f"{url}/json/2/res.users/context_get",
        json={},
        impersonate=_IMPERSONATE,
        timeout=timeout,
        allow_redirects=True,
    )
    summary: Dict[str, Any] = {"status_code": r.status_code}
    supports_json2 = False
    try:
        # Either 401 with Odoo error envelope, or 200 (in dev/no-auth setups
        # which we don't expect in the wild but accept as positive).
        if r.status_code in (401, 200):
            body = r.json()
            name = (body or {}).get("name", "") if isinstance(body, dict) else ""
            supports_json2 = (
                "werkzeug" in name.lower() or "invalid apikey" in str(body).lower()
            ) or r.status_code == 200
            summary["error_name"] = name
    except Exception:
        # 401 without JSON body is still meaningful in context but harder to
        # disambiguate from Cloudflare's challenge page. Stay conservative.
        pass
    summary["supports_json2"] = supports_json2
    return ProbeResult(
        name="json2_unauth",
        ok=supports_json2,
        latency_ms=int((perf_counter() - t0) * 1000),
        status_code=r.status_code,
        summary=summary,
    )


@_timed
def _probe_dns_cloudflare(url: str, timeout: int) -> ProbeResult:
    """Resolve the hostname and check if the IP falls in Cloudflare's ranges."""
    t0 = perf_counter()
    host = urlparse(url).hostname or ""
    ip: Optional[str] = None
    is_cf = False
    try:
        # gethostbyname only returns one A record — good enough for "is it CF".
        ip = socket.gethostbyname(host)
        addr = ipaddress.ip_address(ip)
        if isinstance(addr, ipaddress.IPv4Address):
            is_cf = any(addr in net for net in _CLOUDFLARE_NETS)
    except Exception as e:
        return ProbeResult(
            name="dns_cloudflare",
            ok=False,
            latency_ms=int((perf_counter() - t0) * 1000),
            error=f"{type(e).__name__}: {e}",
        )
    return ProbeResult(
        name="dns_cloudflare",
        ok=True,
        latency_ms=int((perf_counter() - t0) * 1000),
        summary={"ip": ip, "is_cloudflare": is_cf},
    )
