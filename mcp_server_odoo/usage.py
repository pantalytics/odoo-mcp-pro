"""Usage tracking stub for the open source MCP server.

The full version with Postgres tracking, rate limiting, and PostHog
analytics lives in the private odoo-mcp-pro-admin package.

This stub provides a no-op implementation so the public package works
standalone without asyncpg or PostHog dependencies.
"""

from typing import Optional


def track_event(event: str, distinct_id: str = "server", properties: Optional[dict] = None):
    """No-op event tracking. The hosted SaaS deployment ships the real one."""
