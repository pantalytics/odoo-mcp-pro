"""Tests for OAuth 2.1 token verification (ZitadelTokenVerifier).

Tests audience validation, scope checking, error handling, and
the security properties of the token introspection flow.
"""

import base64
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from mcp_server_odoo.oauth import ZitadelTokenVerifier


def _mock_introspection_response(status_code=200, json_data=None):
    """Create a mock httpx.Response for introspection."""
    return httpx.Response(status_code, json=json_data or {})


class TestZitadelTokenVerifier:
    """Test ZitadelTokenVerifier token introspection."""

    @pytest.fixture
    def verifier(self):
        """Create a basic verifier without audience/scope requirements."""
        return ZitadelTokenVerifier(
            introspection_url="https://auth.example.com/oauth/v2/introspect",
            client_id="test-client-id",
            client_secret="test-client-secret",
        )

    def test_auth_header_construction(self, verifier):
        """Test that Basic Auth header is correctly constructed."""
        expected = "Basic " + base64.b64encode(
            b"test-client-id:test-client-secret"
        ).decode()
        assert verifier._auth_header == expected

    @pytest.mark.asyncio
    async def test_active_token_returns_access_token(self, verifier):
        """Test that an active token returns a valid AccessToken."""
        mock_response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "scope": "openid profile email",
            "exp": 9999999999,
        })

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient", return_value=mock_client):
            result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.client_id == "claude-client"
        assert result.scopes == ["openid", "profile", "email"]
        assert result.expires_at == 9999999999

    @pytest.mark.asyncio
    async def test_inactive_token_returns_none(self, verifier):
        """Test that an inactive/revoked token returns None."""
        mock_response = _mock_introspection_response(200, {"active": False})

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient", return_value=mock_client):
            result = await verifier.verify_token("revoked-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_introspection_error_returns_none(self, verifier):
        """Test that introspection endpoint errors return None."""
        mock_response = _mock_introspection_response(500, {"error": "internal"})

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient", return_value=mock_client):
            result = await verifier.verify_token("some-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_introspection_401_returns_none(self, verifier):
        """Test that wrong introspection credentials return None."""
        mock_response = _mock_introspection_response(401, {"error": "unauthorized"})

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient", return_value=mock_client):
            result = await verifier.verify_token("some-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        """Test that introspection timeout returns None (fail closed)."""
        verifier = ZitadelTokenVerifier(
            introspection_url="https://auth.example.com/oauth/v2/introspect",
            client_id="test-client-id",
            client_secret="test-client-secret",
            timeout=1,
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("Connection timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient", return_value=mock_client):
            result = await verifier.verify_token("some-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self, verifier):
        """Test that network errors return None (fail closed)."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient", return_value=mock_client):
            result = await verifier.verify_token("some-token")

        assert result is None


class TestAudienceValidation:
    """Test audience (aud) claim validation."""

    @pytest.fixture
    def verifier(self):
        """Create a verifier with audience validation enabled."""
        return ZitadelTokenVerifier(
            introspection_url="https://auth.example.com/oauth/v2/introspect",
            client_id="test-client-id",
            client_secret="test-client-secret",
            expected_audience="https://mcp.example.com",
        )

    def _make_mock_client(self, response):
        """Helper to create an async mock httpx client."""
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @pytest.mark.asyncio
    async def test_correct_audience_accepted(self, verifier):
        """Test that token with correct audience is accepted."""
        response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "scope": "openid",
            "aud": ["https://mcp.example.com"],
            "exp": 9999999999,
        })

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient",
                    return_value=self._make_mock_client(response)):
            result = await verifier.verify_token("valid-token")

        assert result is not None

    @pytest.mark.asyncio
    async def test_wrong_audience_rejected(self, verifier):
        """Test that token intended for another service is rejected."""
        response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "scope": "openid",
            "aud": ["https://other-service.example.com"],
            "exp": 9999999999,
        })

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient",
                    return_value=self._make_mock_client(response)):
            result = await verifier.verify_token("wrong-audience-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_audience_rejected(self, verifier):
        """Test that token without audience claim is rejected when required."""
        response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "scope": "openid",
            "exp": 9999999999,
            # no 'aud' field
        })

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient",
                    return_value=self._make_mock_client(response)):
            result = await verifier.verify_token("no-audience-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_audience_string_format(self, verifier):
        """Test that audience as string (not array) is handled correctly."""
        response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "scope": "openid",
            "aud": "https://mcp.example.com",  # string, not array
            "exp": 9999999999,
        })

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient",
                    return_value=self._make_mock_client(response)):
            result = await verifier.verify_token("string-aud-token")

        assert result is not None

    @pytest.mark.asyncio
    async def test_multiple_audiences_accepted(self, verifier):
        """Test that token with multiple audiences is accepted if ours is included."""
        response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "scope": "openid",
            "aud": ["https://other.example.com", "https://mcp.example.com"],
            "exp": 9999999999,
        })

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient",
                    return_value=self._make_mock_client(response)):
            result = await verifier.verify_token("multi-aud-token")

        assert result is not None

    @pytest.mark.asyncio
    async def test_no_audience_check_when_not_configured(self):
        """Test that audience is not checked when expected_audience is None."""
        verifier = ZitadelTokenVerifier(
            introspection_url="https://auth.example.com/oauth/v2/introspect",
            client_id="test-client-id",
            client_secret="test-client-secret",
            expected_audience=None,
        )

        response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "scope": "openid",
            "exp": 9999999999,
        })

        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient", return_value=mock_client):
            result = await verifier.verify_token("any-token")

        assert result is not None


class TestScopeValidation:
    """Test scope validation at introspection level."""

    @pytest.fixture
    def verifier(self):
        """Create a verifier with required scopes."""
        return ZitadelTokenVerifier(
            introspection_url="https://auth.example.com/oauth/v2/introspect",
            client_id="test-client-id",
            client_secret="test-client-secret",
            required_scopes=["openid", "profile"],
        )

    def _make_mock_client(self, response):
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @pytest.mark.asyncio
    async def test_all_required_scopes_present(self, verifier):
        """Test that token with all required scopes is accepted."""
        response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "scope": "openid profile email",
            "exp": 9999999999,
        })

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient",
                    return_value=self._make_mock_client(response)):
            result = await verifier.verify_token("valid-token")

        assert result is not None

    @pytest.mark.asyncio
    async def test_missing_required_scope_rejected(self, verifier):
        """Test that token missing a required scope is rejected."""
        response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "scope": "openid",  # missing 'profile'
            "exp": 9999999999,
        })

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient",
                    return_value=self._make_mock_client(response)):
            result = await verifier.verify_token("missing-scope-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_no_scopes_rejected(self, verifier):
        """Test that token with no scopes is rejected when scopes are required."""
        response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "exp": 9999999999,
            # no 'scope' field
        })

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient",
                    return_value=self._make_mock_client(response)):
            result = await verifier.verify_token("no-scope-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_no_scope_check_when_not_configured(self):
        """Test that scopes are not checked when required_scopes is empty."""
        verifier = ZitadelTokenVerifier(
            introspection_url="https://auth.example.com/oauth/v2/introspect",
            client_id="test-client-id",
            client_secret="test-client-secret",
            required_scopes=None,
        )

        response = _mock_introspection_response(200, {
            "active": True,
            "client_id": "claude-client",
            "exp": 9999999999,
        })

        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mcp_server_odoo.oauth.httpx.AsyncClient", return_value=mock_client):
            result = await verifier.verify_token("any-token")

        assert result is not None


class TestOAuthServerSetup:
    """Test OAuth settings construction in the server."""

    def test_oauth_disabled_when_no_issuer_url(self):
        """Test that OAuth is disabled when OAUTH_ISSUER_URL is not set."""
        import os

        env = {
            "OAUTH_ISSUER_URL": "",
            "ZITADEL_INTROSPECTION_URL": "",
            "ZITADEL_CLIENT_ID": "",
            "ZITADEL_CLIENT_SECRET": "",
        }
        with patch.dict(os.environ, env, clear=False):
            from mcp_server_odoo.server import OdooMCPServer

            auth_settings, token_verifier = OdooMCPServer._build_oauth_settings()
            assert auth_settings is None
            assert token_verifier is None

    def test_oauth_raises_when_missing_secret(self):
        """Test that missing ZITADEL_CLIENT_SECRET raises ConfigurationError."""
        import os

        from mcp_server_odoo.error_handling import ConfigurationError

        env = {
            "OAUTH_ISSUER_URL": "https://auth.example.com",
            "ZITADEL_INTROSPECTION_URL": "https://auth.example.com/oauth/v2/introspect",
            "ZITADEL_CLIENT_ID": "test-client",
            "ZITADEL_CLIENT_SECRET": "",
        }
        with patch.dict(os.environ, env, clear=False):
            from mcp_server_odoo.server import OdooMCPServer

            with pytest.raises(ConfigurationError, match="ZITADEL_CLIENT_SECRET"):
                OdooMCPServer._build_oauth_settings()

    def test_oauth_settings_include_required_scopes(self):
        """Test that AuthSettings includes required_scopes when OAuth is enabled."""
        import os

        env = {
            "OAUTH_ISSUER_URL": "https://auth.example.com",
            "ZITADEL_INTROSPECTION_URL": "https://auth.example.com/oauth/v2/introspect",
            "ZITADEL_CLIENT_ID": "test-client",
            "ZITADEL_CLIENT_SECRET": "test-secret",
            "OAUTH_RESOURCE_SERVER_URL": "https://mcp.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            from mcp_server_odoo.server import OdooMCPServer

            auth_settings, token_verifier = OdooMCPServer._build_oauth_settings()

            assert auth_settings is not None
            assert auth_settings.required_scopes == ["openid"]
            assert token_verifier is not None
            assert token_verifier._expected_audience == "https://mcp.example.com"

    def test_oauth_settings_audience_from_resource_url(self):
        """Test that expected_audience is set from OAUTH_RESOURCE_SERVER_URL."""
        import os

        env = {
            "OAUTH_ISSUER_URL": "https://auth.example.com",
            "ZITADEL_INTROSPECTION_URL": "https://auth.example.com/oauth/v2/introspect",
            "ZITADEL_CLIENT_ID": "test-client",
            "ZITADEL_CLIENT_SECRET": "test-secret",
            "OAUTH_RESOURCE_SERVER_URL": "https://mcp.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            from mcp_server_odoo.server import OdooMCPServer

            _, token_verifier = OdooMCPServer._build_oauth_settings()
            assert token_verifier._expected_audience == "https://mcp.example.com"

    def test_oauth_no_audience_without_resource_url(self):
        """Test that expected_audience is None when resource URL is not set."""
        import os

        env = {
            "OAUTH_ISSUER_URL": "https://auth.example.com",
            "ZITADEL_INTROSPECTION_URL": "https://auth.example.com/oauth/v2/introspect",
            "ZITADEL_CLIENT_ID": "test-client",
            "ZITADEL_CLIENT_SECRET": "test-secret",
            "OAUTH_RESOURCE_SERVER_URL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            from mcp_server_odoo.server import OdooMCPServer

            _, token_verifier = OdooMCPServer._build_oauth_settings()
            assert token_verifier._expected_audience is None
