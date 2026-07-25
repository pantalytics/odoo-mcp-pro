"""SSRF: detection probes must not follow redirects.

The setup-time probes SSRF-check only the submitted host. If a probe followed a
redirect, a benign public host could 302 the probe to an internal/metadata
address (e.g. 169.254.169.254) that was never checked, and the response would be
bucketed back into detection evidence (an exfil channel). Every probe HTTP call
must therefore pass allow_redirects=False.
"""

from unittest.mock import MagicMock, patch

from mcp_server_odoo import detection_probes as dp

_HTTP_PROBES = (
    dp._probe_web_version,
    dp._probe_web_health,
    dp._probe_web_login,
    dp._probe_root_headers,
    dp._probe_jsonrpc_version,
    dp._probe_json2_unauth,
)


def test_probes_never_follow_redirects():
    fake = MagicMock()
    resp = MagicMock()
    resp.status_code = 500  # force the non-2xx branch; we only check the call
    fake.get.return_value = resp
    fake.post.return_value = resp
    fake.request.return_value = resp

    with patch.object(dp, "cffi_requests", fake):
        for probe in _HTTP_PROBES:
            try:
                probe("https://acme.example.com", 5)
            except Exception:
                # Parsing the fake response may raise; we only assert how the
                # outbound call was made, which already happened by then.
                pass

    calls = fake.get.call_args_list + fake.post.call_args_list + fake.request.call_args_list
    assert calls, "no probe issued an HTTP call"
    for c in calls:
        assert c.kwargs.get("allow_redirects") is False, (
            f"probe call {c} must not follow redirects (SSRF)"
        )
