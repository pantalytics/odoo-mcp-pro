"""Tests for the public usage stub.

The full UsageTracker (Postgres + PostHog) lives in odoo-mcp-pro-admin
and is tested there. This file only covers what the open source package
exposes: the no-op track_event and OdooToolHandler's no-op tracking hook.
"""

from unittest.mock import MagicMock

from mcp_server_odoo.usage import track_event


class TestTrackEvent:
    def test_returns_none(self):
        """Stub is a no-op; the hosted SaaS deployment ships the real one."""
        assert track_event("test_event") is None
        assert track_event("test_event", distinct_id="user-1", properties={"k": "v"}) is None


class TestToolHandlerTracking:
    def test_track_usage_is_noop_hook(self):
        """_track_usage is an extension hook; the public package never tracks."""
        from mcp_server_odoo.tools import OdooToolHandler

        handler = OdooToolHandler(app=MagicMock())
        assert handler._track_usage("user-123", "search_records") is None
        assert handler._track_usage("stdio", "search_records") is None
