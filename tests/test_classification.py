"""Edition and hosting are decided independently, each from its own probes.

Every case here is drawn from a real server we measured; the docstrings name it
so a future reader can re-check the claim instead of trusting the fixture.
"""

from mcp_server_odoo.classification import (
    combined_confidence,
    decide_edition,
    decide_hosting,
    taxonomy_conflicts,
)
from mcp_server_odoo.detection_probes import ProbeResult

PLATFORM_URL = "https://acme.odoo.com"
SELF_HOSTED_URL = "https://odoo.acme.example"


def _ok(name: str, **summary) -> ProbeResult:
    return ProbeResult(name=name, ok=True, latency_ms=10, summary=summary)


def _fail(name: str) -> ProbeResult:
    return ProbeResult(name=name, ok=False, latency_ms=10)


def _by_name(*probes: ProbeResult):
    return {p.name: p for p in probes}


class TestEdition:
    def test_enterprise_asset_beats_a_missing_version(self):
        """The whole point of the asset probe: it answers when the version
        string does not. Measured on osher1624 (v16) and mole.odoo.com (v15)."""
        edition, conf, _ = decide_edition(
            _by_name(_ok("enterprise_asset", serves_enterprise_asset=True)),
            major=16,
            server_versions=[],
        )
        assert edition == "enterprise"
        assert conf == "high"

    def test_no_enterprise_asset_with_control_means_community(self):
        """bean-forge (19.0): enterprise paths 404, control 200 -> Community."""
        edition, conf, _ = decide_edition(
            _by_name(
                _ok(
                    "enterprise_asset",
                    serves_enterprise_asset=False,
                    serves_control_asset=True,
                )
            ),
            major=19,
            server_versions=["19.0"],
        )
        assert edition == "community"
        assert conf == "high"

    def test_v14_enterprise_is_not_called_community(self):
        """odoo.livelinkmotor.net (14.0+e) serves the control asset but 404s on
        every web_enterprise path -- those only exist from v15. Reading that 404
        as Community would be a confident lie, so the asset probe abstains and
        the version suffix answers."""
        edition, conf, _ = decide_edition(
            _by_name(
                _ok(
                    "enterprise_asset",
                    serves_enterprise_asset=False,
                    serves_control_asset=True,
                )
            ),
            major=14,
            server_versions=["14.0+e"],
        )
        assert edition == "enterprise"
        assert conf == "medium"

    def test_unreadable_control_falls_back_to_the_suffix(self):
        """support.proautomationservices.com (14.0): 404s the control too, so
        the host does not serve addon static files the way we assume."""
        probes = _by_name(
            _ok(
                "enterprise_asset",
                serves_enterprise_asset=False,
                serves_control_asset=False,
            )
        )
        edition, conf, notes = decide_edition(probes, 14, ["14.0"])
        assert edition == "community"
        assert conf == "medium"
        assert any("unreadable" in n for n in notes)

    def test_nothing_to_go_on_returns_none(self):
        edition, conf, _ = decide_edition(_by_name(_fail("enterprise_asset")), None, [])
        assert edition is None
        assert conf == "low"

    def test_hosting_never_leaks_into_the_edition(self):
        """Odoo.sh only runs Enterprise, but that is an inference about the
        product, not a measurement of this server. The edition decision must not
        read the Server header; if the edition probes stay silent, it says so."""
        probes = _by_name(_ok("root_headers", server="Odoo.sh"))
        edition, conf, _ = decide_edition(probes, major=17, server_versions=[])
        assert edition is None
        assert conf == "low"


class TestHosting:
    def test_odoo_sh_server_header(self):
        """pantalytics.odoo.com, georgiesceramic.odoo.com, officeplus."""
        hosting, conf, _ = decide_hosting(
            _by_name(_ok("root_headers", server="Odoo.sh")),
            PLATFORM_URL,
            ["19.0+e"],
            is_odoo=True,
        )
        assert hosting == "sh"
        assert conf == "high"

    def test_saas_version_means_online(self):
        """altaocto.odoo.com, bd-rollers.odoo.com."""
        hosting, conf, _ = decide_hosting(
            _by_name(_ok("root_headers", server="nginx")),
            PLATFORM_URL,
            ["saas~19.2+e"],
            is_odoo=True,
        )
        assert hosting == "online"
        assert conf == "high"

    def test_platform_host_without_sh_header_is_online_at_medium(self):
        """opus-digital.odoo.com: 19.0+e, nginx, no saas~. Odoo hosts only
        Online and Odoo.sh on *.odoo.com and Odoo.sh names itself, so this is
        Online -- but it is read off an absence, hence medium."""
        hosting, conf, notes = decide_hosting(
            _by_name(_ok("root_headers", server="nginx")),
            PLATFORM_URL,
            ["19.0+e"],
            is_odoo=True,
        )
        assert hosting == "online"
        assert conf == "medium"
        assert any("absent_sh_header" in n for n in notes)

    def test_failed_header_probe_is_not_an_absent_header(self):
        """A timed-out probe must not be read as "no Odoo.sh marker". This
        mislabelled georgiesceramic (Odoo.sh) as Online when its header probe
        timed out under load."""
        hosting, conf, notes = decide_hosting(
            _by_name(_fail("root_headers")),
            PLATFORM_URL,
            ["17.0+e"],
            is_odoo=True,
        )
        assert hosting == "unknown"
        assert conf == "low"
        assert any("undecidable" in n for n in notes)

    def test_self_hosted_off_platform(self):
        """juffermans.cloudpepper.site, bean-forge.cloudpepper.site."""
        hosting, conf, _ = decide_hosting(
            _by_name(_ok("root_headers", server="nginx/1.30.3")),
            SELF_HOSTED_URL,
            ["19.0"],
            is_odoo=True,
        )
        assert hosting == "self_hosted"
        assert conf == "high"

    def test_not_an_odoo_is_unknown(self):
        hosting, conf, _ = decide_hosting(
            _by_name(_ok("root_headers", server="Apache")),
            SELF_HOSTED_URL,
            [],
            is_odoo=False,
        )
        assert hosting == "unknown"
        assert conf == "low"

    def test_edition_never_leaks_into_the_hosting(self):
        """Community rules out Odoo's platforms, but hosting is measured, not
        inferred from the edition. Same probes, same answer, whatever the
        edition turns out to be."""
        probes = _by_name(_ok("root_headers", server="Odoo.sh"))
        for versions in (["19.0+e"], ["19.0"], []):
            hosting, conf, _ = decide_hosting(probes, PLATFORM_URL, versions, True)
            assert (hosting, conf) == ("sh", "high")


class TestTaxonomy:
    def test_impossible_pair_is_reported_not_fixed(self):
        """Both axes were measured, so we cannot tell which probe lied. Saying
        so beats silently dropping whichever one a rule distrusts."""
        assert taxonomy_conflicts("community", "sh")
        assert taxonomy_conflicts("community", "online")

    def test_real_pairs_are_quiet(self):
        assert taxonomy_conflicts("enterprise", "sh") == []
        assert taxonomy_conflicts("enterprise", "online") == []
        assert taxonomy_conflicts("enterprise", "self_hosted") == []
        assert taxonomy_conflicts("community", "self_hosted") == []

    def test_unknowns_are_never_conflicts(self):
        assert taxonomy_conflicts(None, "sh") == []
        assert taxonomy_conflicts("community", "unknown") == []


class TestCombinedConfidence:
    def test_takes_the_weaker_axis(self):
        assert combined_confidence("high", "medium") == "medium"
        assert combined_confidence("low", "high") == "low"
        assert combined_confidence("high", "high") == "high"
