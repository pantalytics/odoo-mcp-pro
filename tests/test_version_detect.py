"""Tests for the Odoo API version auto-detection module."""

from unittest.mock import MagicMock, patch

import pytest

from mcp_server_odoo.version_detect import JSON2_MIN_VERSION, _parse_major, detect_api_version


class TestDetectApiVersion:
    """Test detect_api_version function."""

    def test_detects_json2_for_odoo_19(self):
        """Odoo 19+ should return json2."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {
            "server_version": "19.0",
            "server_version_info": [19, 0, 0, "final", 0],
        }

        with patch(
            "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
        ):
            api_version, server_version = detect_api_version("https://mycompany.odoo.com")

        assert api_version == "json2"
        assert server_version == "19.0"

    def test_detects_xmlrpc_for_odoo_17(self):
        """Odoo 17 should return xmlrpc."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {
            "server_version": "17.0",
            "server_version_info": [17, 0, 0, "final", 0],
        }

        with patch(
            "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
        ):
            api_version, server_version = detect_api_version("https://mycompany.odoo.com")

        assert api_version == "xmlrpc"
        assert server_version == "17.0"

    def test_detects_xmlrpc_for_odoo_14(self):
        """Odoo 14 should return xmlrpc."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {
            "server_version": "14.0",
            "server_version_info": [14, 0, 0, "final", 0],
        }

        with patch(
            "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
        ):
            api_version, server_version = detect_api_version("https://mycompany.odoo.com")

        assert api_version == "xmlrpc"
        assert server_version == "14.0"

    def test_detects_json2_for_odoo_20(self):
        """Future Odoo 20+ should also return json2."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {
            "server_version": "20.0",
            "server_version_info": [20, 0, 0, "final", 0],
        }

        with patch(
            "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
        ):
            api_version, server_version = detect_api_version("https://mycompany.odoo.com")

        assert api_version == "json2"
        assert server_version == "20.0"

    def test_fallback_to_server_version_string(self):
        """Should parse version from server_version string when server_version_info is missing."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {
            "server_version": "19.0",
            "server_version_info": [],
        }

        with patch(
            "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
        ):
            api_version, server_version = detect_api_version("https://mycompany.odoo.com")

        assert api_version == "json2"
        assert server_version == "19.0"

    def test_unknown_when_both_probes_fail(self):
        """When xmlrpc raises AND /web/version is unreachable, return 'unknown'."""
        mock_proxy = MagicMock()
        mock_proxy.version.side_effect = ConnectionRefusedError("refused")

        with (
            patch(
                "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
            ),
            patch("mcp_server_odoo.version_detect._detect_via_web_version", return_value=None),
        ):
            api_version, server_version = detect_api_version("https://unreachable.example.com")

        assert api_version == "unknown"
        assert server_version is None

    def test_unknown_on_timeout_with_both_probes_failing(self):
        """xmlrpc timeout + /web/version unreachable -> unknown."""
        mock_proxy = MagicMock()
        mock_proxy.version.side_effect = TimeoutError("timeout")

        with (
            patch(
                "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
            ),
            patch("mcp_server_odoo.version_detect._detect_via_web_version", return_value=None),
        ):
            api_version, server_version = detect_api_version("https://slow.example.com")

        assert api_version == "unknown"
        assert server_version is None

    def test_unknown_when_xmlrpc_returns_empty(self):
        """Empty xmlrpc response + /web/version unreachable -> unknown."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {}

        with (
            patch(
                "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
            ),
            patch("mcp_server_odoo.version_detect._detect_via_web_version", return_value=None),
        ):
            api_version, server_version = detect_api_version("https://mycompany.odoo.com")

        assert api_version == "unknown"
        assert server_version is None

    def test_web_version_fallback_when_xmlrpc_fails(self):
        """xmlrpc 301/connection-error -> /web/version succeeds with curl_cffi."""
        mock_proxy = MagicMock()
        mock_proxy.version.side_effect = ConnectionError("301 Moved Permanently")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "version": "saas~19.2+e",
            "version_info": ["saas~19", 2, 0, "final", 0, "e"],
        }

        with (
            patch(
                "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
            ),
            patch("mcp_server_odoo.version_detect.cffi_requests.get", return_value=mock_resp),
        ):
            api_version, server_version = detect_api_version("https://cf-protected.example.com")

        assert api_version == "json2"
        assert server_version == "saas~19.2+e"

    def test_web_version_fallback_returns_xmlrpc_for_v18(self):
        """xmlrpc fails -> /web/version returns v18 -> xmlrpc."""
        mock_proxy = MagicMock()
        mock_proxy.version.side_effect = ConnectionError("blocked")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"version": "18.0", "version_info": [18, 0, 0, "final", 0]}

        with (
            patch(
                "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
            ),
            patch("mcp_server_odoo.version_detect.cffi_requests.get", return_value=mock_resp),
        ):
            api_version, server_version = detect_api_version("https://example.com")

        assert api_version == "xmlrpc"
        assert server_version == "18.0"

    def test_url_trailing_slash_stripped(self):
        """Should strip trailing slash from URL."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {
            "server_version": "19.0",
            "server_version_info": [19, 0, 0, "final", 0],
        }

        with patch(
            "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
        ) as mock_cls:
            detect_api_version("https://mycompany.odoo.com/")

        mock_cls.assert_called_once_with(
            "https://mycompany.odoo.com/xmlrpc/2/common",
            allow_none=True,
        )

    def test_json2_min_version_constant(self):
        """JSON2_MIN_VERSION should be 19."""
        assert JSON2_MIN_VERSION == 19

    def test_detects_json2_at_boundary(self):
        """Exactly version 19 should return json2."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {
            "server_version": "19.0",
            "server_version_info": [19, 0, 0, "alpha", 1],
        }

        with patch(
            "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
        ):
            api_version, _ = detect_api_version("https://mycompany.odoo.com")

        assert api_version == "json2"

    def test_detects_xmlrpc_at_boundary(self):
        """Version 18 should return xmlrpc."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {
            "server_version": "18.0",
            "server_version_info": [18, 0, 0, "final", 0],
        }

        with patch(
            "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
        ):
            api_version, _ = detect_api_version("https://mycompany.odoo.com")

        assert api_version == "xmlrpc"


class TestParseMajor:
    """Test _parse_major helper for various Odoo version formats."""

    def test_integer_value(self):
        assert _parse_major(19) == 19

    def test_simple_string(self):
        assert _parse_major("19") == 19

    def test_saas_tilde_format(self):
        """Odoo.sh SaaS versions like 'saas~19'."""
        assert _parse_major("saas~19") == 19

    def test_saas_tilde_with_minor(self):
        """Full SaaS version string like 'saas~19.2+e'."""
        assert _parse_major("saas~19.2+e") == 19

    def test_dotted_string(self):
        assert _parse_major("17.0") == 17

    def test_raises_on_no_digits(self):
        with pytest.raises(ValueError, match="Cannot parse major version"):
            _parse_major("unknown")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError, match="Cannot parse major version"):
            _parse_major("")


class TestDetectSaasVersions:
    """Test detect_api_version with Odoo.sh SaaS version strings."""

    def test_saas_19_detected_as_json2(self):
        """Odoo.sh saas~19 should return json2."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {
            "server_version": "saas~19.2+e",
            "server_version_info": ["saas~19", 2, 0, "final", 0, "e"],
        }

        with patch(
            "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
        ):
            api_version, server_version = detect_api_version("https://cg-folks.odoo.com")

        assert api_version == "json2"
        assert server_version == "saas~19.2+e"

    def test_saas_17_detected_as_xmlrpc(self):
        """Odoo.sh saas~17 should return xmlrpc."""
        mock_proxy = MagicMock()
        mock_proxy.version.return_value = {
            "server_version": "saas~17.4",
            "server_version_info": ["saas~17", 4, 0, "final", 0, ""],
        }

        with patch(
            "mcp_server_odoo.version_detect.xmlrpc.client.ServerProxy", return_value=mock_proxy
        ):
            api_version, server_version = detect_api_version("https://example.odoo.com")

        assert api_version == "xmlrpc"
        assert server_version == "saas~17.4"


class TestConfigAutoApiVersion:
    """Test OdooConfig always defaults to auto-detection."""

    def test_auto_is_default(self):
        """api_version should default to 'auto'."""
        from mcp_server_odoo.config import OdooConfig

        config = OdooConfig(url="https://odoo.example.com", api_key="test-key")
        assert config.api_version == "auto"

    def test_accepts_api_key(self):
        """Config should accept api_key auth."""
        from mcp_server_odoo.config import OdooConfig

        config = OdooConfig(url="https://odoo.example.com", api_key="test-key")
        assert config.uses_api_key

    def test_accepts_credentials(self):
        """Config should accept username/password auth."""
        from mcp_server_odoo.config import OdooConfig

        config = OdooConfig(
            url="https://odoo.example.com",
            username="user",
            password="pass",
        )
        assert config.uses_credentials

    def test_rejects_no_auth(self):
        """Config should reject missing auth."""
        from mcp_server_odoo.config import OdooConfig

        with pytest.raises(ValueError, match="Authentication required"):
            OdooConfig(url="https://odoo.example.com")

    def test_no_odoo_api_version_env_var(self):
        """ODOO_API_VERSION env var should not affect config."""
        import os

        from mcp_server_odoo.config import load_config, reset_config

        reset_config()
        # Even if the env var is set, load_config ignores it
        os.environ["ODOO_URL"] = "https://odoo.example.com"
        os.environ["ODOO_API_KEY"] = "test-key"
        os.environ["ODOO_API_VERSION"] = "json2"  # should be ignored
        try:
            config = load_config()
            assert config.api_version == "auto"
        finally:
            os.environ.pop("ODOO_API_VERSION", None)
            os.environ.pop("ODOO_URL", None)
            os.environ.pop("ODOO_API_KEY", None)
            reset_config()
