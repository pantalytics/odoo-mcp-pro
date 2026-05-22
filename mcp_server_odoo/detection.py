"""Odoo server detection — shotgun probes + evidence aggregation.

Instead of a sequential P1 → P2 → user-prompt detection ladder (one probe,
fall back to the next on failure), this module fires *every* probe we know
about in parallel and aggregates the evidence. Trade-off:

- Slower than P1-happy-path (worst case ~max(probe latencies)) but no worse
  than the old fallback when P1 failed.
- One probe being blocked / lying no longer ruins the answer — the others
  still come back with data and the aggregator votes.
- The full evidence is persistable (see admin's `detection_evidence` JSONB
  column) so production weirdness can be diagnosed without rerunning.

Probes implemented:

  1. xmlrpc_version       — stdlib XML-RPC version() on /xmlrpc/2/common
  2. web_version          — GET /web/version (curl_cffi, Chrome TLS)
  3. web_health           — GET /web/health (Odoo 17+ liveness)
  4. web_login            — GET /web/login (HTML markers, db_monodb cookie)
  5. root_headers         — GET / (server: nginx / Odoo.sh / cloudflare)
  6. jsonrpc_version      — POST /jsonrpc with common.version
  7. json2_unauth         — POST /json/2/res.users/context_get unauthenticated
                            (401 "Invalid apikey" → JSON/2 endpoint live → v19+)
  8. dns_cloudflare       — DNS A lookup + Cloudflare IP-range check

The aggregator turns this into a DetectionResult with api_version,
server_version, hosting, edition, behind_waf, and a confidence label.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlparse

from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)

JSON2_MIN_VERSION = 19
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


ApiVersion = Literal["json2", "xmlrpc", "unknown"]
Hosting = Literal["online", "sh", "self_hosted", "behind_waf", "unknown"]
Edition = Literal["community", "enterprise"]
Confidence = Literal["high", "medium", "low"]


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


@dataclass
class DetectionResult:
    is_odoo: bool
    api_version: ApiVersion
    server_version: Optional[str]
    major: Optional[int]
    hosting: Hosting
    edition: Optional[Edition]
    behind_waf: bool
    confidence: Confidence
    probes: List[ProbeResult]
    conflicts: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_odoo": self.is_odoo,
            "api_version": self.api_version,
            "server_version": self.server_version,
            "major": self.major,
            "hosting": self.hosting,
            "edition": self.edition,
            "behind_waf": self.behind_waf,
            "confidence": self.confidence,
            "conflicts": self.conflicts,
            "probes": [p.to_dict() for p in self.probes],
        }


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


_PROBES = (
    _probe_xmlrpc_version,
    _probe_web_version,
    _probe_web_health,
    _probe_web_login,
    _probe_root_headers,
    _probe_jsonrpc_version,
    _probe_json2_unauth,
    _probe_dns_cloudflare,
)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def _aggregate(probes: List[ProbeResult]) -> DetectionResult:
    """Rule-based fusion of probe outputs into a single conclusion.

    Voting strategy:
    - is_odoo: positive if ANY of (xmlrpc_version, web_version, web_login,
      jsonrpc_version, json2_unauth) succeeded.
    - major version: majority vote across probes that produced a major.
      Ties broken by preferring the highest version (forward-compat: we'd
      rather try json2 on a v18-v19 boundary instance).
    - api_version: json2 if json2_unauth said so OR majority major >= 19.
    - hosting: Cloudflare server-header or CF IP range → behind_waf.
      Server header "Odoo.sh" → sh. "saas~" version prefix → online.
      Otherwise unknown.
    - edition: "+e" suffix in any server_version → enterprise, else community
      (only when at least one version probe succeeded; otherwise None).
    - confidence: high if ≥3 probes contributed congruent signals;
      medium with 2; low with 0 or 1.
    """
    by_name = {p.name: p for p in probes}
    conflicts: List[str] = []

    odoo_signals = [
        by_name.get("xmlrpc_version"),
        by_name.get("web_version"),
        by_name.get("web_login"),
        by_name.get("jsonrpc_version"),
        by_name.get("json2_unauth"),
    ]
    is_odoo = any(p and p.ok for p in odoo_signals)

    # Major version vote
    majors: List[int] = []
    server_versions: List[str] = []
    for name in ("xmlrpc_version", "web_version", "jsonrpc_version"):
        p = by_name.get(name)
        if not p or not p.ok:
            continue
        m = p.summary.get("major")
        sv = p.summary.get("server_version") or ""
        if isinstance(m, int):
            majors.append(m)
        if sv:
            server_versions.append(sv)

    major: Optional[int] = None
    if majors:
        # Mode with tie-break toward highest
        from collections import Counter

        counts = Counter(majors).most_common()
        top_count = counts[0][1]
        tied = [v for v, c in counts if c == top_count]
        major = max(tied)
        if len(set(majors)) > 1:
            conflicts.append(f"major_version_disagreement: probes returned {majors}")

    # JSON/2 evidence is independently authoritative — overrides version vote
    # when /json/2 endpoint responds, because that endpoint only exists on v19+.
    json2_probe = by_name.get("json2_unauth")
    json2_alive = bool(json2_probe and json2_probe.ok)

    if json2_alive:
        api_version: ApiVersion = "json2"
        if major is not None and major < JSON2_MIN_VERSION:
            conflicts.append(f"json2_endpoint_live_but_version_probe_says_v{major}")
            # Trust json2: the version probe is likely cached or wrong.
            major = max(major, JSON2_MIN_VERSION)
    elif major is not None:
        api_version = "json2" if major >= JSON2_MIN_VERSION else "xmlrpc"
    else:
        api_version = "unknown"

    server_version = server_versions[0] if server_versions else None

    # Hosting
    headers_probe = by_name.get("root_headers")
    server_hdr = (headers_probe.summary.get("server") if headers_probe else "") or ""
    dns_probe = by_name.get("dns_cloudflare")
    is_cf_ip = bool(dns_probe and dns_probe.summary.get("is_cloudflare"))
    is_cf_hdr = "cloudflare" in server_hdr.lower()
    behind_waf = is_cf_ip or is_cf_hdr

    hosting: Hosting
    if behind_waf:
        hosting = "behind_waf"
    elif "Odoo.sh" in server_hdr:
        hosting = "sh"
    elif any("saas~" in sv for sv in server_versions):
        hosting = "online"
    elif is_odoo:
        # nginx + non-saas version is ambiguous (could be Online or .sh).
        # Don't claim certainty — see reference_odoo_taxonomy.md.
        hosting = "self_hosted"
    else:
        hosting = "unknown"

    # Edition (only meaningful when we have a version string)
    edition: Optional[Edition] = None
    if server_versions:
        edition = "enterprise" if any("+e" in sv for sv in server_versions) else "community"

    # Confidence: count probes that contributed to the conclusion
    contributing = sum(
        1
        for p in probes
        if p.ok
        and p.name
        in {
            "xmlrpc_version",
            "web_version",
            "jsonrpc_version",
            "json2_unauth",
            "web_health",
            "web_login",
            "root_headers",
            "dns_cloudflare",
        }
    )
    if contributing >= 3:
        confidence: Confidence = "high"
    elif contributing == 2:
        confidence = "medium"
    else:
        confidence = "low"

    return DetectionResult(
        is_odoo=is_odoo,
        api_version=api_version,
        server_version=server_version,
        major=major,
        hosting=hosting,
        edition=edition,
        behind_waf=behind_waf,
        confidence=confidence,
        probes=list(probes),
        conflicts=conflicts,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def detect_odoo(url: str, timeout: int = PROBE_TIMEOUT_SECONDS) -> DetectionResult:
    """Fire every probe in parallel and aggregate the evidence."""
    url = url.rstrip("/")
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=len(_PROBES)) as pool:
        tasks = [loop.run_in_executor(pool, p, url, timeout) for p in _PROBES]
        probes: List[ProbeResult] = list(await asyncio.gather(*tasks))
    result = _aggregate(probes)
    logger.info(
        "Detected %s: api=%s major=%s hosting=%s waf=%s confidence=%s (probes_ok=%d/%d)",
        url,
        result.api_version,
        result.major,
        result.hosting,
        result.behind_waf,
        result.confidence,
        sum(1 for p in probes if p.ok),
        len(probes),
    )
    return result


def detect_odoo_sync(url: str, timeout: int = PROBE_TIMEOUT_SECONDS) -> DetectionResult:
    """Sync convenience wrapper around `detect_odoo`."""
    return asyncio.run(detect_odoo(url, timeout))


def detect_api_version(
    odoo_url: str,
    timeout: int = PROBE_TIMEOUT_SECONDS,
) -> Tuple[ApiVersion, Optional[str]]:
    """Back-compat shim that returns just (api_version, server_version).

    Existing callers (server.py, registry.py) don't need the full evidence
    matrix; they just want to know which connection class to instantiate.
    They get that here; the admin layer calls `detect_odoo` directly to
    persist the evidence.
    """
    result = detect_odoo_sync(odoo_url, timeout)
    return result.api_version, result.server_version
