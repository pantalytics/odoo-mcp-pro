"""Auto-detect Odoo API version from server version.

Probes the Odoo server via two independent paths and returns the
appropriate API version string:

1. **P1** — `xmlrpc.client.version()` against `/xmlrpc/2/common`. Stdlib,
   fast, no extra deps. Works on every Odoo from 14+ that exposes XML-RPC.
   Its stdlib TLS fingerprint usually slips past Cloudflare bot-detection.

2. **P2** — `curl_cffi.get('/web/version')` with Chrome impersonation.
   Slower fallback, but Chrome-spoofed TLS passes through every WAF we've
   observed in production. Used when P1 raises any exception or when the
   response is unparseable.

If both fail, we return `("unknown", None)`. Callers can then either fall
back to a default (server.py treats unknown as xmlrpc) or surface a user
prompt (admin setup wizard asks the user to pick a version).
"""

import logging
import re
import xmlrpc.client
from typing import Literal, Optional, Tuple

from curl_cffi import requests as cffi_requests
from curl_cffi.requests.errors import RequestsError

from .xmlrpc_transport import transport_for_url

logger = logging.getLogger(__name__)

# Minimum Odoo major version that supports JSON/2 API
JSON2_MIN_VERSION = 19

# Regex to extract the major version number from strings like "saas~19", "19", "19.0"
_MAJOR_VERSION_RE = re.compile(r"(\d+)")

ApiVersion = Literal["json2", "xmlrpc", "unknown"]


def _parse_major(value) -> int:
    """Extract major version number from version identifier.

    Handles: 19 (int), "19", "19.0", "saas~19", "saas~19.2+e"
    """
    if isinstance(value, int):
        return value
    match = _MAJOR_VERSION_RE.search(str(value))
    if match:
        return int(match.group(1))
    raise ValueError(f"Cannot parse major version from {value!r}")


def _api_version_for(major: int) -> Literal["json2", "xmlrpc"]:
    return "json2" if major >= JSON2_MIN_VERSION else "xmlrpc"


def _detect_via_xmlrpc(url: str, timeout: int) -> Optional[Tuple[Literal["json2", "xmlrpc"], str]]:
    """P1: XML-RPC version() probe. Returns None on any failure."""
    try:
        proxy = xmlrpc.client.ServerProxy(
            f"{url}/xmlrpc/2/common",
            allow_none=True,
            transport=transport_for_url(url, timeout),
        )
        info = proxy.version()
    except Exception as e:
        logger.info(f"xmlrpc probe failed at {url}: {e}")
        return None

    server_version = info.get("server_version", "")
    server_version_info = info.get("server_version_info", [])

    try:
        if server_version_info and len(server_version_info) >= 1:
            major = _parse_major(server_version_info[0])
        elif server_version:
            major = _parse_major(server_version.split(".")[0])
        else:
            return None
    except ValueError:
        return None

    return _api_version_for(major), server_version


def _detect_via_web_version(
    url: str, timeout: int
) -> Optional[Tuple[Literal["json2", "xmlrpc"], str]]:
    """P2: GET /web/version via curl_cffi (Chrome TLS).

    Used when XML-RPC probe fails — most commonly because of customer-side
    Cloudflare rejecting our stdlib TLS fingerprint. Browser-impersonated
    TLS passes through.

    Returns None if the endpoint also blocks us or the response is unusable.
    """
    try:
        resp = cffi_requests.get(
            f"{url}/web/version",
            impersonate="chrome",
            timeout=timeout,
            allow_redirects=True,
        )
    except RequestsError as e:
        logger.info(f"/web/version probe failed at {url}: {e}")
        return None

    if resp.status_code != 200:
        logger.info(f"/web/version returned {resp.status_code} at {url}")
        return None

    try:
        data = resp.json()
    except Exception:
        return None

    # /web/version returns {"version": "saas~19.2+e", "version_info": [...]}
    server_version = data.get("version") or data.get("server_version", "")
    version_info = data.get("version_info") or data.get("server_version_info") or []

    try:
        if version_info and len(version_info) >= 1:
            major = _parse_major(version_info[0])
        elif server_version:
            major = _parse_major(server_version.split(".")[0])
        else:
            return None
    except ValueError:
        return None

    return _api_version_for(major), server_version


def detect_api_version(
    odoo_url: str,
    timeout: int = 10,
) -> Tuple[ApiVersion, Optional[str]]:
    """Detect the appropriate API version for an Odoo server.

    Tries XML-RPC first, then /web/version via curl_cffi as a Cloudflare-aware
    fallback. Returns ("unknown", None) when both probes fail — callers should
    either default to xmlrpc (OSS standalone) or ask the user (admin wizard).

    Args:
        odoo_url: Base URL of the Odoo server (e.g., "https://mycompany.odoo.com")
        timeout: Per-probe connection timeout in seconds

    Returns:
        Tuple of (api_version, server_version_string).
        api_version is "json2" for Odoo 19+, "xmlrpc" for older versions,
        "unknown" if both probes failed.
        server_version_string is e.g. "saas~19.2+e" or None when unknown.
    """
    url = odoo_url.rstrip("/")

    result = _detect_via_xmlrpc(url, timeout)
    if result is not None:
        api_version, server_version = result
        logger.info(f"Detected Odoo {server_version} via xmlrpc -> api_version={api_version}")
        return api_version, server_version

    result = _detect_via_web_version(url, timeout)
    if result is not None:
        api_version, server_version = result
        logger.info(
            f"Detected Odoo {server_version} via /web/version (curl_cffi) "
            f"-> api_version={api_version}"
        )
        return api_version, server_version

    logger.warning(
        f"Both detection probes failed at {url}. Returning 'unknown' so the "
        f"caller can prompt the user."
    )
    return "unknown", None
