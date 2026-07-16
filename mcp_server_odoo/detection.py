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

Probes implemented (see `detection_probes`):

  1. xmlrpc_version       — stdlib XML-RPC version() on /xmlrpc/2/common
  2. web_version          — GET /web/version (curl_cffi, Chrome TLS)
  3. web_health           — GET /web/health (Odoo 17+ liveness)
  4. web_login            — GET /web/login (HTML markers, db_monodb cookie)
  5. root_headers         — GET / (server: nginx / Odoo.sh / cloudflare)
  6. jsonrpc_version      — POST /jsonrpc with common.version
  7. json2_unauth         — POST /json/2/res.users/context_get unauthenticated
                            (401 "Invalid apikey" → JSON/2 endpoint live → v19+)
  8. dns_cloudflare       — DNS A lookup + Cloudflare IP-range check
  9. enterprise_asset     — GET a web_enterprise static file + a control file
                            (Community does not ship web_enterprise)

The aggregator turns this into a DetectionResult with api_version,
server_version, hosting, edition, behind_waf, and confidence labels.

Edition and hosting are separate questions and are answered separately, each
from its own probes -- see `classification` for why deriving one from the other
loses information. behind_waf is orthogonal to both.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

from .classification import (  # noqa: F401  (types re-exported for callers)
    Confidence,
    Edition,
    Hosting,
    combined_confidence,
    decide_edition,
    decide_hosting,
    taxonomy_conflicts,
)
from .detection_probes import (  # noqa: F401  (ProbeResult/_parse_major re-exported)
    PROBE_TIMEOUT_SECONDS,
    ProbeResult,
    _parse_major,
    _probe_dns_cloudflare,
    _probe_enterprise_asset,
    _probe_json2_unauth,
    _probe_jsonrpc_version,
    _probe_root_headers,
    _probe_web_health,
    _probe_web_login,
    _probe_web_version,
    _probe_xmlrpc_version,
)

logger = logging.getLogger(__name__)

JSON2_MIN_VERSION = 19


ApiVersion = Literal["json2", "xmlrpc", "unknown"]


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
    # Per axis, because they are not equally knowable on the same server: a v14
    # yields a confident hosting and an edition we can only read off "+e".
    # `confidence` stays the weaker of the two for callers wanting one number.
    edition_confidence: Confidence = "low"
    hosting_confidence: Confidence = "low"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_odoo": self.is_odoo,
            "api_version": self.api_version,
            "server_version": self.server_version,
            "major": self.major,
            "hosting": self.hosting,
            "hosting_confidence": self.hosting_confidence,
            "edition": self.edition,
            "edition_confidence": self.edition_confidence,
            "behind_waf": self.behind_waf,
            "confidence": self.confidence,
            "conflicts": self.conflicts,
            "probes": [p.to_dict() for p in self.probes],
        }


_PROBES = (
    _probe_xmlrpc_version,
    _probe_web_version,
    _probe_web_health,
    _probe_web_login,
    _probe_root_headers,
    _probe_jsonrpc_version,
    _probe_json2_unauth,
    _probe_dns_cloudflare,
    _probe_enterprise_asset,
)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def _aggregate(probes: List[ProbeResult], url: str) -> DetectionResult:
    """Rule-based fusion of probe outputs into a single conclusion.

    Voting strategy:
    - is_odoo: positive if ANY of (xmlrpc_version, web_version, web_login,
      jsonrpc_version, json2_unauth) succeeded.
    - major version: majority vote across probes that produced a major.
      Ties broken by preferring the highest version (forward-compat: we'd
      rather try json2 on a v18-v19 boundary instance).
    - api_version: json2 if json2_unauth said so OR majority major >= 19.
    - edition / hosting: see `classification`. Independent axes, each with its
      own probes and its own confidence.
    - behind_waf: Cloudflare server-header or CF IP range. Orthogonal to
      hosting, never a substitute for it.
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

    # A WAF sits on the network path; it is not a place the software runs. It
    # is reported next to the hosting, never instead of it -- overwriting the
    # hosting with "behind_waf" is what left the column unable to answer a
    # hosting question for every Cloudflare-fronted customer.
    headers_probe = by_name.get("root_headers")
    server_hdr = (headers_probe.summary.get("server") if headers_probe else "") or ""
    dns_probe = by_name.get("dns_cloudflare")
    behind_waf = bool(dns_probe and dns_probe.summary.get("is_cloudflare")) or (
        "cloudflare" in server_hdr.lower()
    )

    # Two axes, decided from their own probes and blind to each other. The
    # taxonomy is checked afterwards and only reports contradictions: both
    # answers were measured, so an impossible pair means a probe lied and we
    # cannot tell which.
    edition, edition_confidence, edition_notes = decide_edition(by_name, major, server_versions)
    hosting, hosting_confidence, hosting_notes = decide_hosting(
        by_name, url, server_versions, is_odoo
    )
    conflicts.extend(edition_notes)
    conflicts.extend(hosting_notes)
    conflicts.extend(taxonomy_conflicts(edition, hosting))

    return DetectionResult(
        is_odoo=is_odoo,
        api_version=api_version,
        server_version=server_version,
        major=major,
        hosting=hosting,
        edition=edition,
        behind_waf=behind_waf,
        confidence=combined_confidence(edition_confidence, hosting_confidence),
        edition_confidence=edition_confidence,
        hosting_confidence=hosting_confidence,
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
    result = _aggregate(probes, url)
    logger.info(
        "Detected %s: api=%s major=%s hosting=%s(%s) edition=%s(%s) waf=%s (probes_ok=%d/%d)",
        url,
        result.api_version,
        result.major,
        result.hosting,
        result.hosting_confidence,
        result.edition,
        result.edition_confidence,
        result.behind_waf,
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
