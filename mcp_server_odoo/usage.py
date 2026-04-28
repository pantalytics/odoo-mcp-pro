"""Usage tracking stub for the open source MCP server.

The full version with Postgres tracking, rate limiting, and PostHog
analytics is in the odoo-mcp-pro-admin package.

This stub provides no-op implementations so the public package works
standalone without asyncpg or PostHog dependencies.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Default daily limit (used by full UsageTracker in admin package)
DEFAULT_DAILY_LIMIT = 1000


def track_event(event: str, distinct_id: str = "server", properties: Optional[dict] = None):
    """No-op event tracking. Install odoo-mcp-pro-admin for PostHog integration."""
    pass


class SessionLifecycleMiddleware:
    """No-op middleware. Install odoo-mcp-pro-admin for session lifecycle tracking."""

    def __init__(self, app, path_prefix: str = "/mcp"):
        self.app = app

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


class RateLimitExceeded(Exception):
    """Raised when a user exceeds their daily call limit."""

    def __init__(self, limit: int, used: int):
        self.limit = limit
        self.used = used
        super().__init__(f"Daily rate limit exceeded: {used}/{limit} calls used today.")
