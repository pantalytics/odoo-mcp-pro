"""Tests for the public usage stub.

The full UsageTracker (Postgres + PostHog) lives in odoo-mcp-pro-admin
and is tested there. This file only covers what the open source package
exposes: DEFAULT_DAILY_LIMIT, RateLimitExceeded, track_event,
SessionLifecycleMiddleware, and OdooToolHandler's tracker integration.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_server_odoo.usage import (
    DEFAULT_DAILY_LIMIT,
    RateLimitExceeded,
    SessionLifecycleMiddleware,
    track_event,
)


class TestDefaultDailyLimit:
    def test_value(self):
        assert DEFAULT_DAILY_LIMIT == 1000


class TestRateLimitExceeded:
    def test_attributes(self):
        exc = RateLimitExceeded(50, 50)
        assert exc.limit == 50
        assert exc.used == 50

    def test_message_includes_counts(self):
        exc = RateLimitExceeded(100, 150)
        assert "150" in str(exc)
        assert "100" in str(exc)


class TestTrackEvent:
    def test_returns_none(self):
        """Stub is a no-op; install admin package for real tracking."""
        assert track_event("test_event") is None
        assert track_event("test_event", distinct_id="user-1", properties={"k": "v"}) is None


class TestSessionLifecycleMiddleware:
    @pytest.mark.asyncio
    async def test_passes_through(self):
        app = AsyncMock()
        middleware = SessionLifecycleMiddleware(app)

        scope = {"type": "http"}
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)
        app.assert_awaited_once_with(scope, receive, send)


class TestToolHandlerTracking:
    """OdooToolHandler delegates to the injected tracker (if any)."""

    def test_track_usage_calls_fire_and_forget(self):
        from mcp_server_odoo.tools import OdooToolHandler

        mock_tracker = MagicMock()
        handler = OdooToolHandler(
            app=MagicMock(),
            usage_tracker=mock_tracker,
        )

        handler._track_usage("user-123", "search_records")
        mock_tracker.record_usage_fire_and_forget.assert_called_once_with(
            "user-123", "search_records"
        )

    def test_track_usage_skips_stdio(self):
        from mcp_server_odoo.tools import OdooToolHandler

        mock_tracker = MagicMock()
        handler = OdooToolHandler(
            app=MagicMock(),
            usage_tracker=mock_tracker,
        )

        handler._track_usage("stdio", "search_records")
        mock_tracker.record_usage_fire_and_forget.assert_not_called()

    def test_track_usage_noop_without_tracker(self):
        from mcp_server_odoo.tools import OdooToolHandler

        handler = OdooToolHandler(app=MagicMock())

        handler._track_usage("user-123", "search_records")
