"""Integration tests for DCR helper against a real Zitadel instance.

Unlike test_oauth_dcr.py (httpx-mocked), these tests drive the helper
against a live Zitadel on localhost:8085. Required to catch real
response-shape issues (missing fields, proto vs JSON gateway
differences, devMode/appType nulls, etc.).

Prerequisites (see odoo-mcp-pro-admin deploy/ for the scripts):
    docker compose -f deploy/docker-compose.local-test.yml up -d zitadel-db zitadel
    bash deploy/setup-zitadel.sh http://localhost:8085
    bash deploy/create-dcr-app.sh http://localhost:8085

Then set these env vars to whatever create-dcr-app.sh printed:
    ZITADEL_TEST_URL=http://localhost:8085
    ZITADEL_TEST_PAT=<contents of deploy/machinekey/admin.pat>
    ZITADEL_TEST_PROJECT_ID=<printed by create-dcr-app.sh>
    ZITADEL_TEST_APP_ID=<printed by create-dcr-app.sh>

Tests auto-skip if any env var is missing or Zitadel isn't reachable.
"""

import os
import socket

import httpx
import pytest

from mcp_server_odoo.server import (
    _append_redirect_uris_to_dcr_app,
    _DCRUpdateError,
)

pytestmark = pytest.mark.integration


REQUIRED_ENV = [
    "ZITADEL_TEST_URL",
    "ZITADEL_TEST_PAT",
    "ZITADEL_TEST_PROJECT_ID",
    "ZITADEL_TEST_APP_ID",
]


def _zitadel_reachable(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((parsed.hostname or "localhost", parsed.port or 80))
        sock.close()
        return result == 0
    except Exception:
        return False


def _env_missing() -> list[str]:
    return [k for k in REQUIRED_ENV if not os.getenv(k, "").strip()]


if _env_missing():
    pytest.skip(
        f"skipping DCR integration: missing env {_env_missing()}",
        allow_module_level=True,
    )

if not _zitadel_reachable(os.environ["ZITADEL_TEST_URL"]):
    pytest.skip(
        f"skipping DCR integration: Zitadel not reachable at {os.environ['ZITADEL_TEST_URL']}",
        allow_module_level=True,
    )


BOOTSTRAP_URI = "https://example.invalid/dcr-bootstrap"


@pytest.fixture
def cfg():
    return {
        "url": os.environ["ZITADEL_TEST_URL"].rstrip("/"),
        "pat": os.environ["ZITADEL_TEST_PAT"],
        "project_id": os.environ["ZITADEL_TEST_PROJECT_ID"],
        "app_id": os.environ["ZITADEL_TEST_APP_ID"],
    }


def _get_redirect_uris(cfg) -> list[str]:
    r = httpx.get(
        f"{cfg['url']}/management/v1/projects/{cfg['project_id']}/apps/{cfg['app_id']}",
        headers={"Authorization": f"Bearer {cfg['pat']}"},
        timeout=10,
    )
    r.raise_for_status()
    return (r.json().get("app") or {}).get("oidcConfig", {}).get("redirectUris") or []


def _reset_redirect_uris(cfg) -> None:
    """Reset the DCR app's redirectUris to just the bootstrap URI.

    PUT is full-replace; we preserve all existing OIDC fields and only
    override redirectUris. Matches what _append_redirect_uris_to_dcr_app
    does internally.
    """
    r = httpx.get(
        f"{cfg['url']}/management/v1/projects/{cfg['project_id']}/apps/{cfg['app_id']}",
        headers={"Authorization": f"Bearer {cfg['pat']}"},
        timeout=10,
    )
    r.raise_for_status()
    oidc = (r.json().get("app") or {}).get("oidcConfig") or {}
    put_body = {
        k: v
        for k, v in {
            "redirectUris": [BOOTSTRAP_URI],
            "responseTypes": oidc.get("responseTypes"),
            "grantTypes": oidc.get("grantTypes"),
            "appType": oidc.get("appType"),
            "authMethodType": oidc.get("authMethodType"),
            "postLogoutRedirectUris": oidc.get("postLogoutRedirectUris"),
            "devMode": oidc.get("devMode"),
        }.items()
        if v is not None
    }
    r = httpx.put(
        f"{cfg['url']}/management/v1/projects/{cfg['project_id']}/apps/{cfg['app_id']}/oidc_config",
        headers={"Authorization": f"Bearer {cfg['pat']}"},
        json=put_body,
        timeout=10,
    )
    # Zitadel returns 400 "No changes" when PUT is a no-op (state already
    # matches). Fine for reset purposes.
    if r.status_code == 400 and "No changes" in r.text:
        return
    r.raise_for_status()


@pytest.fixture(autouse=True)
def clean_state(cfg):
    """Reset the DCR app to just the bootstrap URI before each test."""
    _reset_redirect_uris(cfg)
    yield
    _reset_redirect_uris(cfg)


class TestDCRAgainstRealZitadel:
    @pytest.mark.asyncio
    async def test_appends_chatgpt_uri(self, cfg):
        uri = "https://chatgpt.com/connector/oauth/int-test-1"
        await _append_redirect_uris_to_dcr_app(
            zitadel_base_url=cfg["url"],
            pat=cfg["pat"],
            project_id=cfg["project_id"],
            app_id=cfg["app_id"],
            new_uris=[uri],
        )
        uris = _get_redirect_uris(cfg)
        assert uri in uris
        assert BOOTSTRAP_URI in uris, "bootstrap URI must be preserved"

    @pytest.mark.asyncio
    async def test_idempotent_same_uri_twice(self, cfg):
        """Re-registering the same URI must not duplicate it."""
        uri = "https://chatgpt.com/connector/oauth/int-test-2"
        for _ in range(2):
            await _append_redirect_uris_to_dcr_app(
                zitadel_base_url=cfg["url"],
                pat=cfg["pat"],
                project_id=cfg["project_id"],
                app_id=cfg["app_id"],
                new_uris=[uri],
            )
        uris = _get_redirect_uris(cfg)
        assert uris.count(uri) == 1

    @pytest.mark.asyncio
    async def test_accumulates_across_calls(self, cfg):
        """Two separate /register calls with different URIs both land."""
        uri_a = "https://chatgpt.com/connector/oauth/int-test-a"
        uri_b = "https://chatgpt.com/connector/oauth/int-test-b"

        await _append_redirect_uris_to_dcr_app(
            zitadel_base_url=cfg["url"],
            pat=cfg["pat"],
            project_id=cfg["project_id"],
            app_id=cfg["app_id"],
            new_uris=[uri_a],
        )
        await _append_redirect_uris_to_dcr_app(
            zitadel_base_url=cfg["url"],
            pat=cfg["pat"],
            project_id=cfg["project_id"],
            app_id=cfg["app_id"],
            new_uris=[uri_b],
        )
        uris = _get_redirect_uris(cfg)
        assert uri_a in uris
        assert uri_b in uris

    @pytest.mark.asyncio
    async def test_multi_uri_single_call(self, cfg):
        a = "https://chatgpt.com/connector/oauth/int-multi-a"
        b = "https://chat.openai.com/connector/oauth/int-multi-b"
        await _append_redirect_uris_to_dcr_app(
            zitadel_base_url=cfg["url"],
            pat=cfg["pat"],
            project_id=cfg["project_id"],
            app_id=cfg["app_id"],
            new_uris=[a, b],
        )
        uris = _get_redirect_uris(cfg)
        assert a in uris and b in uris

    @pytest.mark.asyncio
    async def test_wrong_app_id_raises(self, cfg):
        with pytest.raises(_DCRUpdateError, match="GET app failed"):
            await _append_redirect_uris_to_dcr_app(
                zitadel_base_url=cfg["url"],
                pat=cfg["pat"],
                project_id=cfg["project_id"],
                app_id="000000000000000",
                new_uris=["https://chatgpt.com/x"],
            )

    @pytest.mark.asyncio
    async def test_invalid_pat_raises(self, cfg):
        with pytest.raises(_DCRUpdateError, match="GET app failed"):
            await _append_redirect_uris_to_dcr_app(
                zitadel_base_url=cfg["url"],
                pat="invalid-pat-xxx",
                project_id=cfg["project_id"],
                app_id=cfg["app_id"],
                new_uris=["https://chatgpt.com/x"],
            )
