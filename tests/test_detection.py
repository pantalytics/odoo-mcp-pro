"""Tests for the shotgun detection module."""

from unittest.mock import patch

from mcp_server_odoo.detection import (
    ProbeResult,
    _aggregate,
    _parse_major,
    detect_odoo_sync,
)

# ---------------------------------------------------------------------------
# Pure aggregator tests — no I/O
# ---------------------------------------------------------------------------


def _ok(name: str, **summary) -> ProbeResult:
    return ProbeResult(name=name, ok=True, latency_ms=100, summary=summary)


def _fail(name: str, status_code=None) -> ProbeResult:
    return ProbeResult(name=name, ok=False, latency_ms=100, status_code=status_code)


class TestAggregator:
    """The aggregator is a pure function over ProbeResult lists."""

    def test_v19_online_happy_path(self):
        probes = [
            _ok("xmlrpc_version", server_version="saas~19.2+e", major=19),
            _ok("web_version", server_version="saas~19.2+e", major=19),
            _ok("jsonrpc_version", server_version="saas~19.2+e", major=19),
            _ok("json2_unauth", supports_json2=True),
            _ok("root_headers", server="nginx"),
            _ok("dns_cloudflare", ip="1.2.3.4", is_cloudflare=False),
        ]
        r = _aggregate(probes)
        assert r.is_odoo
        assert r.api_version == "json2"
        assert r.major == 19
        assert r.server_version == "saas~19.2+e"
        assert r.hosting == "online"
        assert r.edition == "enterprise"
        assert not r.behind_waf
        assert r.confidence == "high"
        assert r.conflicts == []

    def test_v18_self_hosted(self):
        probes = [
            _ok("xmlrpc_version", server_version="18.0+e", major=18),
            _ok("jsonrpc_version", server_version="18.0+e", major=18),
            _fail("json2_unauth", status_code=404),
            _ok("root_headers", server="Werkzeug"),
            _ok("dns_cloudflare", is_cloudflare=False),
        ]
        r = _aggregate(probes)
        assert r.api_version == "xmlrpc"
        assert r.major == 18
        assert r.hosting == "self_hosted"
        assert r.edition == "enterprise"

    def test_behind_cloudflare_by_dns(self):
        probes = [
            _ok("web_version", server_version="saas~19.2+e", major=19),
            _ok("json2_unauth", supports_json2=True),
            _ok("dns_cloudflare", ip="104.21.13.7", is_cloudflare=True),
            _ok("root_headers", server="cloudflare"),
        ]
        r = _aggregate(probes)
        assert r.behind_waf
        assert r.hosting == "behind_waf"
        assert r.api_version == "json2"

    def test_odoo_sh_via_server_header(self):
        probes = [
            _ok("xmlrpc_version", server_version="19.0+e", major=19),
            _ok("root_headers", server="Odoo.sh"),
            _ok("dns_cloudflare", is_cloudflare=False),
        ]
        r = _aggregate(probes)
        assert r.hosting == "sh"

    def test_json2_endpoint_overrides_version_vote(self):
        """If /json/2 responds, treat the instance as v19+ even if version
        probes are cached and say v18 (real production gotcha)."""
        probes = [
            _ok("xmlrpc_version", server_version="18.0+e", major=18),
            _ok("json2_unauth", supports_json2=True),
            _ok("root_headers", server="nginx"),
        ]
        r = _aggregate(probes)
        assert r.api_version == "json2"
        assert r.major >= 19
        assert any("json2_endpoint_live" in c for c in r.conflicts)

    def test_disagreement_in_major_recorded_as_conflict(self):
        probes = [
            _ok("xmlrpc_version", server_version="19.0+e", major=19),
            _ok("web_version", server_version="18.0+e", major=18),
            _fail("json2_unauth"),
        ]
        r = _aggregate(probes)
        # Tie broken by max
        assert r.major == 19
        assert any("disagreement" in c for c in r.conflicts)

    def test_no_probes_succeeded_returns_unknown(self):
        probes = [
            _fail("xmlrpc_version"),
            _fail("web_version"),
            _fail("web_health"),
            _fail("web_login"),
            _fail("jsonrpc_version"),
            _fail("json2_unauth"),
            _fail("root_headers"),
            _fail("dns_cloudflare"),
        ]
        r = _aggregate(probes)
        assert not r.is_odoo
        assert r.api_version == "unknown"
        assert r.major is None
        assert r.hosting == "unknown"
        assert r.confidence == "low"

    def test_only_dns_and_headers_no_odoo(self):
        """Reachable host but no Odoo markers."""
        probes = [
            _fail("xmlrpc_version"),
            _fail("web_version", status_code=200),  # ok=False because no major
            _ok("dns_cloudflare", ip="1.2.3.4", is_cloudflare=False),
            _ok("root_headers", server="Apache"),
        ]
        r = _aggregate(probes)
        assert not r.is_odoo
        assert r.api_version == "unknown"

    def test_confidence_medium_with_two_probes(self):
        probes = [
            _ok("xmlrpc_version", server_version="19.0+e", major=19),
            _ok("root_headers", server="nginx"),
            _fail("web_version"),
            _fail("json2_unauth"),
        ]
        r = _aggregate(probes)
        assert r.confidence == "medium"

    def test_to_dict_round_trips(self):
        probes = [_ok("xmlrpc_version", server_version="19.0+e", major=19)]
        r = _aggregate(probes)
        d = r.to_dict()
        # major=19 -> json2 even without a live json2 probe; the json2
        # endpoint becomes the override when present, not a requirement.
        assert d["api_version"] == "json2"
        assert d["major"] == 19
        assert d["server_version"] == "19.0+e"
        assert "probes" in d and len(d["probes"]) == 1


class TestParseMajor:
    def test_int_passthrough(self):
        assert _parse_major(19) == 19

    def test_saas_string(self):
        assert _parse_major("saas~19.2+e") == 19

    def test_dotted_string(self):
        assert _parse_major("19.0") == 19

    def test_none_returns_none(self):
        assert _parse_major(None) is None

    def test_empty_returns_none(self):
        assert _parse_major("") is None

    def test_garbage_returns_none(self):
        assert _parse_major("not-a-version") is None


# ---------------------------------------------------------------------------
# detect_odoo_sync with mocked probes — confirms wiring works end-to-end
# ---------------------------------------------------------------------------


class TestDetectOdooSync:
    """Mock the probe functions so the runner doesn't hit the network."""

    def _make_probe_result(self, name, ok=True, **summary):
        return ProbeResult(name=name, ok=ok, latency_ms=42, summary=summary)

    def test_all_probes_run_and_aggregate(self):
        from mcp_server_odoo import detection

        fake_probes = [
            lambda url, t, n=p.__name__: self._make_probe_result(
                n.replace("_probe_", ""),
                ok=True,
                **({"server_version": "saas~19.2+e", "major": 19} if "version" in n else {}),
            )
            for p in detection._PROBES
        ]
        with patch.object(detection, "_PROBES", tuple(fake_probes)):
            r = detect_odoo_sync("https://example.odoo.com")
        assert r.is_odoo
        assert r.major == 19
        assert len(r.probes) == len(detection._PROBES)
