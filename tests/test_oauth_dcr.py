"""Tests for Dynamic Client Registration (DCR) logic in server.py.

Covers the Zitadel management-API helper and the host allowlist used
by the /register endpoint. The endpoint itself is exercised end-to-end
by the admin-repo Playwright suite; here we test the unit pieces that
can fail silently.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from mcp_server_odoo.server import (
    _DCR_ALLOWED_HOSTS,
    _DCR_STATIC_HOSTS,
    _append_redirect_uris_to_dcr_app,
    _DCRUpdateError,
    _resolve_dcr_env,
    _resolve_static_client_id,
    _resolve_static_client_secret,
)


def _mock_client(responses):
    """Build an AsyncMock that replays the given responses in order.

    Each response is a (method_name, httpx.Response) tuple.
    """
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[r for m, r in responses if m == "get"])
    client.put = AsyncMock(side_effect=[r for m, r in responses if m == "put"])
    return client


def _get_response(existing_uris, extra=None):
    """Build a mock GET response for a Zitadel app with the given URIs."""
    return httpx.Response(
        200,
        json={
            "app": {
                "oidcConfig": {
                    "redirectUris": list(existing_uris),
                    "responseTypes": ["OIDC_RESPONSE_TYPE_CODE"],
                    "grantTypes": ["OIDC_GRANT_TYPE_AUTHORIZATION_CODE"],
                    "appType": "OIDC_APP_TYPE_WEB",
                    "authMethodType": "OIDC_AUTH_METHOD_TYPE_NONE",
                    "devMode": False,
                    **(extra or {}),
                },
            },
        },
    )


class TestDCRAllowlist:
    """The hardcoded host allowlist is a security boundary; pin it."""

    def test_chatgpt_and_claude_are_allowed(self):
        assert "chatgpt.com" in _DCR_ALLOWED_HOSTS
        assert "chat.openai.com" in _DCR_ALLOWED_HOSTS
        assert "claude.ai" in _DCR_ALLOWED_HOSTS

    def test_lechat_is_allowed(self):
        assert "callback.mistral.ai" in _DCR_ALLOWED_HOSTS

    def test_localhost_allowed_for_dev(self):
        assert "localhost" in _DCR_ALLOWED_HOSTS
        assert "127.0.0.1" in _DCR_ALLOWED_HOSTS

    def test_arbitrary_hosts_rejected(self):
        for evil in ("evil.com", "google.com", "chatgpt.com.evil.com", ""):
            assert evil not in _DCR_ALLOWED_HOSTS

    def test_static_hosts_subset_of_allowed(self):
        assert _DCR_STATIC_HOSTS.issubset(_DCR_ALLOWED_HOSTS)

    def test_chatgpt_is_dynamic_not_static(self):
        assert "chatgpt.com" not in _DCR_STATIC_HOSTS
        assert "chat.openai.com" not in _DCR_STATIC_HOSTS

    def test_lechat_is_static(self):
        # Mistral uses one fixed callback URL across all customers, so
        # the redirect URI is pre-declared on the Zitadel app at setup
        # time — no DCR mutation needed.
        assert "callback.mistral.ai" in _DCR_STATIC_HOSTS


class TestResolveStaticClientId:
    """Per-AI host → client_id mapping with new+old env name fallback."""

    def test_claude_prefers_new_env_name(self, monkeypatch):
        monkeypatch.setenv("MCP_CLAUDE_CLIENT_ID", "new-claude-id")
        monkeypatch.setenv("MCP_OIDC_CLIENT_ID", "old-claude-id")
        assert _resolve_static_client_id("claude.ai") == "new-claude-id"

    def test_claude_falls_back_to_old_env_name(self, monkeypatch):
        monkeypatch.delenv("MCP_CLAUDE_CLIENT_ID", raising=False)
        monkeypatch.setenv("MCP_OIDC_CLIENT_ID", "old-claude-id")
        assert _resolve_static_client_id("claude.ai") == "old-claude-id"

    def test_localhost_resolves_to_claude_app(self, monkeypatch):
        # Localhost is dev-only and shares the Claude Zitadel app.
        monkeypatch.setenv("MCP_CLAUDE_CLIENT_ID", "claude-id")
        assert _resolve_static_client_id("localhost") == "claude-id"
        assert _resolve_static_client_id("127.0.0.1") == "claude-id"

    def test_lechat_uses_dedicated_env(self, monkeypatch):
        monkeypatch.setenv("MCP_LECHAT_CLIENT_ID", "lechat-id")
        monkeypatch.setenv("MCP_CLAUDE_CLIENT_ID", "claude-id")
        # Lechat must NOT pick up Claude's id even with old fallback set.
        monkeypatch.setenv("MCP_OIDC_CLIENT_ID", "old-claude-id")
        assert _resolve_static_client_id("callback.mistral.ai") == "lechat-id"

    def test_unknown_host_returns_empty(self):
        # Defensive: even if somehow allowlisted, an unmapped static host
        # returns "" so /register fails loudly rather than silently
        # returning the wrong client_id.
        assert _resolve_static_client_id("evil.com") == ""

    def test_unset_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("MCP_LECHAT_CLIENT_ID", raising=False)
        assert _resolve_static_client_id("callback.mistral.ai") == ""


class TestResolveStaticClientSecret:
    """Le Chat is the only confidential static client; everyone else is public."""

    def test_lechat_returns_configured_secret(self, monkeypatch):
        monkeypatch.setenv("MCP_LECHAT_CLIENT_SECRET", "shh-secret")
        assert _resolve_static_client_secret("callback.mistral.ai") == "shh-secret"

    def test_lechat_unset_returns_empty(self, monkeypatch):
        monkeypatch.delenv("MCP_LECHAT_CLIENT_SECRET", raising=False)
        assert _resolve_static_client_secret("callback.mistral.ai") == ""

    def test_claude_is_public_client_no_secret(self, monkeypatch):
        # Claude uses PKCE only — no secret env var, helper returns "".
        monkeypatch.setenv("MCP_LECHAT_CLIENT_SECRET", "should-not-leak")
        assert _resolve_static_client_secret("claude.ai") == ""
        assert _resolve_static_client_secret("localhost") == ""

    def test_unknown_host_returns_empty(self):
        assert _resolve_static_client_secret("evil.com") == ""


class TestResolveDcrEnv:
    """ChatGPT (dynamic-DCR) env config: new+old name fallback."""

    def test_prefers_new_env_names(self, monkeypatch):
        monkeypatch.setenv("MCP_CHATGPT_CLIENT_ID", "new-cid")
        monkeypatch.setenv("MCP_CHATGPT_APP_ID", "new-aid")
        monkeypatch.setenv("MCP_CHATGPT_PROJECT_ID", "new-pid")
        monkeypatch.setenv("MCP_OIDC_DCR_CLIENT_ID", "old-cid")
        monkeypatch.setenv("MCP_OIDC_DCR_APP_ID", "old-aid")
        monkeypatch.setenv("MCP_OIDC_DCR_PROJECT_ID", "old-pid")
        env = _resolve_dcr_env()
        assert env == {"client_id": "new-cid", "app_id": "new-aid", "project_id": "new-pid"}

    def test_falls_back_to_old_env_names(self, monkeypatch):
        for new in ("MCP_CHATGPT_CLIENT_ID", "MCP_CHATGPT_APP_ID", "MCP_CHATGPT_PROJECT_ID"):
            monkeypatch.delenv(new, raising=False)
        monkeypatch.setenv("MCP_OIDC_DCR_CLIENT_ID", "old-cid")
        monkeypatch.setenv("MCP_OIDC_DCR_APP_ID", "old-aid")
        monkeypatch.setenv("MCP_OIDC_DCR_PROJECT_ID", "old-pid")
        env = _resolve_dcr_env()
        assert env == {"client_id": "old-cid", "app_id": "old-aid", "project_id": "old-pid"}

    def test_unset_returns_empty_strings(self, monkeypatch):
        for var in (
            "MCP_CHATGPT_CLIENT_ID",
            "MCP_CHATGPT_APP_ID",
            "MCP_CHATGPT_PROJECT_ID",
            "MCP_OIDC_DCR_CLIENT_ID",
            "MCP_OIDC_DCR_APP_ID",
            "MCP_OIDC_DCR_PROJECT_ID",
        ):
            monkeypatch.delenv(var, raising=False)
        env = _resolve_dcr_env()
        assert env == {"client_id": "", "app_id": "", "project_id": ""}


class TestAppendRedirectUrisToDcrApp:
    """The helper that mutates the DCR app's redirectUris via Zitadel."""

    @pytest.mark.asyncio
    async def test_appends_new_uri_and_puts(self):
        """Happy path: new URI added, PUT called with merged list."""
        new_uri = "https://chatgpt.com/connector/oauth/abc"
        mock_client = _mock_client(
            [
                ("get", _get_response(["https://claude.ai/oauth/callback"])),
                ("put", httpx.Response(200, json={"details": {}})),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _append_redirect_uris_to_dcr_app(
                zitadel_base_url="https://auth.example.com",
                pat="pat_xxx",
                project_id="proj-1",
                app_id="app-1",
                new_uris=[new_uri],
            )

        assert mock_client.put.call_count == 1
        put_kwargs = mock_client.put.call_args.kwargs
        put_body = put_kwargs["json"]
        assert new_uri in put_body["redirectUris"]
        assert "https://claude.ai/oauth/callback" in put_body["redirectUris"]
        # All preserved OIDC fields must be included
        assert put_body["authMethodType"] == "OIDC_AUTH_METHOD_TYPE_NONE"
        assert put_body["appType"] == "OIDC_APP_TYPE_WEB"

    @pytest.mark.asyncio
    async def test_skips_put_when_uri_already_registered(self):
        """Idempotency: repeat register of an existing URI skips PUT."""
        existing = "https://chatgpt.com/connector/oauth/abc"
        mock_client = _mock_client(
            [
                ("get", _get_response([existing])),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _append_redirect_uris_to_dcr_app(
                zitadel_base_url="https://auth.example.com",
                pat="pat_xxx",
                project_id="proj-1",
                app_id="app-1",
                new_uris=[existing],
            )

        mock_client.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_deduplicates_partially_new_uris(self):
        """Mix of new + existing URIs: PUT contains merged set without dupes."""
        existing = "https://chatgpt.com/connector/oauth/abc"
        new = "https://chatgpt.com/connector/oauth/xyz"
        mock_client = _mock_client(
            [
                ("get", _get_response([existing])),
                ("put", httpx.Response(200, json={"details": {}})),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _append_redirect_uris_to_dcr_app(
                zitadel_base_url="https://auth.example.com",
                pat="pat_xxx",
                project_id="proj-1",
                app_id="app-1",
                new_uris=[existing, new],
            )

        put_body = mock_client.put.call_args.kwargs["json"]
        assert sorted(put_body["redirectUris"]) == sorted([existing, new])

    @pytest.mark.asyncio
    async def test_get_non_200_raises(self):
        """GET failure surfaces as _DCRUpdateError, not silent skip."""
        mock_client = _mock_client(
            [
                ("get", httpx.Response(404, text="not found")),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(_DCRUpdateError, match="GET app failed: 404"):
                await _append_redirect_uris_to_dcr_app(
                    zitadel_base_url="https://auth.example.com",
                    pat="pat_xxx",
                    project_id="proj-1",
                    app_id="wrong-app",
                    new_uris=["https://chatgpt.com/x"],
                )

    @pytest.mark.asyncio
    async def test_put_400_no_changes_is_treated_as_success(self):
        """Race: concurrent /register already added our URI. Zitadel
        returns 400 'No changes'. Idempotent, not an error."""
        mock_client = _mock_client(
            [
                ("get", _get_response([])),
                (
                    "put",
                    httpx.Response(
                        400,
                        json={"code": 9, "message": "No changes (COMMAND-1m88i)"},
                    ),
                ),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            # Must not raise
            await _append_redirect_uris_to_dcr_app(
                zitadel_base_url="https://auth.example.com",
                pat="pat_xxx",
                project_id="proj-1",
                app_id="app-1",
                new_uris=["https://chatgpt.com/x"],
            )

    @pytest.mark.asyncio
    async def test_put_non_200_raises(self):
        mock_client = _mock_client(
            [
                ("get", _get_response([])),
                ("put", httpx.Response(403, text="forbidden")),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(_DCRUpdateError, match="PUT config failed: 403"):
                await _append_redirect_uris_to_dcr_app(
                    zitadel_base_url="https://auth.example.com",
                    pat="pat_xxx",
                    project_id="proj-1",
                    app_id="app-1",
                    new_uris=["https://chatgpt.com/x"],
                )

    @pytest.mark.asyncio
    async def test_missing_oidc_config_raises(self):
        """Wrong app_id (e.g. API app instead of OIDC app) is caught."""
        mock_client = _mock_client(
            [
                ("get", httpx.Response(200, json={"app": {"apiConfig": {}}})),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(_DCRUpdateError, match="no oidcConfig"):
                await _append_redirect_uris_to_dcr_app(
                    zitadel_base_url="https://auth.example.com",
                    pat="pat_xxx",
                    project_id="proj-1",
                    app_id="api-app",
                    new_uris=["https://chatgpt.com/x"],
                )

    @pytest.mark.asyncio
    async def test_network_error_on_get_raises(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("down"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(_DCRUpdateError, match="network error"):
                await _append_redirect_uris_to_dcr_app(
                    zitadel_base_url="https://auth.example.com",
                    pat="pat_xxx",
                    project_id="proj-1",
                    app_id="app-1",
                    new_uris=["https://chatgpt.com/x"],
                )

    @pytest.mark.asyncio
    async def test_trailing_slash_in_base_url_handled(self):
        """zitadel_base_url may come in with or without trailing slash."""
        mock_client = _mock_client(
            [
                ("get", _get_response([])),
                ("put", httpx.Response(200, json={})),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _append_redirect_uris_to_dcr_app(
                zitadel_base_url="https://auth.example.com/",
                pat="pat_xxx",
                project_id="proj-1",
                app_id="app-1",
                new_uris=["https://chatgpt.com/x"],
            )

        get_url = mock_client.get.call_args.args[0]
        assert "//" not in get_url.split("://", 1)[1]
