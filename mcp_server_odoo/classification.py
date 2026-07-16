"""Edition and hosting: two independent axes, each decided by its own probes.

These are separate questions about a server and they are answered separately.
Deriving one from the other loses real information: an Odoo Online instance is
also Enterprise, and collapsing that into a single "online" label throws the
edition away. A Cloudflare-fronted Odoo still runs on some hosting; "behind a
WAF" is a property of the network path, not a place the software runs.

So: `decide_edition` and `decide_hosting` never read each other's output.
`_taxonomy_conflicts` compares the two afterwards and reports pairs that cannot
exist, which is a signal that a probe lied -- not an input to either decision.

Each returns its own confidence, because the axes are not equally knowable for
a given server: a v14 host can yield a confident hosting and an edition we can
only guess from the version string.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Tuple

from .detection_probes import ENTERPRISE_ASSET_MIN_VERSION, ProbeResult

Hosting = Literal["online", "sh", "self_hosted", "unknown"]
Edition = Literal["community", "enterprise"]
Confidence = Literal["high", "medium", "low"]

# Odoo Online and Odoo.sh both serve from here, so the domain alone never
# separates them -- but it does rule out self-hosting, which is what makes the
# absence of the Odoo.sh header readable.
_ODOO_PLATFORM_DOMAIN = ".odoo.com"

# Odoo.sh's proxy announces itself in the Server header. Measured present on
# every Odoo.sh host we have and absent on every Odoo Online host we have.
_ODOO_SH_SERVER_HEADER = "odoo.sh"

# Odoo Online runs its rolling releases on saas~ versions. The stable ones
# report a plain "19.0+e", so the absence of saas~ says nothing on its own.
_ONLINE_VERSION_MARKER = "saas~"

# Pairs that cannot exist: Odoo does not host Community on either platform.
_IMPOSSIBLE = frozenset({("community", "online"), ("community", "sh")})

_RANK: Dict[Confidence, int] = {"high": 3, "medium": 2, "low": 1}


def is_odoo_platform_host(url: str) -> bool:
    """True when the URL is on Odoo's own hosting domain (*.odoo.com)."""
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    host = host.rstrip(".")
    return host == "odoo.com" or host.endswith(_ODOO_PLATFORM_DOMAIN)


def decide_edition(
    by_name: Dict[str, ProbeResult],
    major: Optional[int],
    server_versions: List[str],
) -> Tuple[Optional[Edition], Confidence, List[str]]:
    """Community or Enterprise, from the strongest evidence available.

    Ranked, because the signals are not equally trustworthy:

    1. The web_enterprise asset answered. Community does not ship that addon,
       so a 200 is proof. A 404 is proof of Community only once we know the
       server is new enough to have the path AND that it serves addon static
       files at all (the control asset) -- otherwise the probe abstains.
    2. The "+e" version suffix. Weaker: it is absent whenever the version
       probes are blocked, and it is the signal the asset probe exists to
       replace. Kept as a second vote, and it is all we have on v14.
    """
    notes: List[str] = []
    asset = by_name.get("enterprise_asset")
    summary = asset.summary if asset else {}

    if summary.get("serves_enterprise_asset"):
        return "enterprise", "high", notes

    asset_says_community = (
        summary.get("serves_control_asset")
        and major is not None
        and major >= ENTERPRISE_ASSET_MIN_VERSION
    )
    if asset_says_community:
        return "community", "high", notes

    if asset and summary.get("serves_control_asset") and major is None:
        notes.append("enterprise_asset_inconclusive: no major version to read it against")

    suffix_edition: Optional[Edition] = None
    if server_versions:
        suffix_edition = "enterprise" if any("+e" in v for v in server_versions) else "community"

    if suffix_edition is None:
        return None, "low", notes

    if asset and not summary.get("serves_control_asset"):
        notes.append("enterprise_asset_unreadable: host does not serve the control asset")
    return suffix_edition, "medium", notes


def decide_hosting(
    by_name: Dict[str, ProbeResult],
    url: str,
    server_versions: List[str],
    is_odoo: bool,
) -> Tuple[Hosting, Confidence, List[str]]:
    """Odoo Online, Odoo.sh, or the customer's own infrastructure.

    Deliberately says nothing about a WAF: `behind_waf` is reported alongside
    this, not instead of it. A Cloudflare-fronted Odoo.sh is still Odoo.sh, and
    overwriting the hosting with "behind_waf" is what made the column unable to
    answer a hosting question.
    """
    notes: List[str] = []
    headers = by_name.get("root_headers")
    header_read = bool(headers and headers.ok)
    server_hdr = ((headers.summary.get("server") if headers else "") or "").lower()

    if _ODOO_SH_SERVER_HEADER in server_hdr:
        return "sh", "high", notes
    if any(_ONLINE_VERSION_MARKER in v for v in server_versions):
        return "online", "high", notes
    if not is_odoo:
        return "unknown", "low", notes
    if is_odoo_platform_host(url):
        # Odoo hosts exactly two things here and Odoo.sh names itself in the
        # Server header, so its absence points at Online. Only readable if the
        # header probe actually answered: a timed-out probe is not an absent
        # header, and treating it as one labels Odoo.sh hosts Online whenever
        # the network hiccups.
        if not header_read:
            notes.append("hosting_undecidable: header probe failed, cannot read the sh marker")
            return "unknown", "low", notes
        # Medium, not high: reading an absence, so an Odoo.sh that ever stops
        # sending the header would land here wrongly.
        notes.append("hosting_from_absent_sh_header: *.odoo.com and no Odoo.sh marker")
        return "online", "medium", notes
    return "self_hosted", "high", notes


def taxonomy_conflicts(edition: Optional[Edition], hosting: Hosting) -> List[str]:
    """Report edition/hosting pairs that cannot exist. Never fixes them.

    Both axes were measured independently, so an impossible pair means one of
    the probes is wrong and we do not know which. Saying so is more useful than
    silently dropping whichever one a rule happens to distrust.
    """
    if edition is None or hosting == "unknown":
        return []
    if (edition, hosting) in _IMPOSSIBLE:
        return [f"impossible_taxonomy: edition={edition} on hosting={hosting}"]
    return []


def combined_confidence(edition_conf: Confidence, hosting_conf: Confidence) -> Confidence:
    """The weaker of the two, for callers that want one number."""
    return edition_conf if _RANK[edition_conf] <= _RANK[hosting_conf] else hosting_conf
