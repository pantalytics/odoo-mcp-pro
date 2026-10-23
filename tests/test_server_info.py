"""Tests for the server_info_get tool's connection-status reporting.

Shared tool fixtures live in tests/helpers/tool_fixtures.py.
"""

import pytest

from tests.helpers.tool_fixtures import (
    handler,
    mock_access_controller,
    mock_app,
    mock_connection,
    valid_config,
)

# Re-export shared fixtures so pytest can resolve them in this module
__all__ = [
    "handler",
    "mock_access_controller",
    "mock_app",
    "mock_connection",
    "valid_config",
]


class TestServerInfo:
    """server_info_get should explain WHY it is not connected, not just that it isn't."""

    @pytest.mark.asyncio
    async def test_surfaces_connection_error(self, handler, mock_app):
        """When the connection cannot be resolved, the failure reason is
        reported in `error` (alongside connected=False) so the AI client can
        relay it to the user instead of a bare "not connected"."""

        async def _raise():
            raise Exception("Authentication failed: Invalid apikey")

        handler._get_user_context = _raise

        result = await mock_app._tools["server_info_get"]()

        assert result.connected is False
        assert result.error is not None
        assert "Invalid apikey" in result.error

    @pytest.mark.asyncio
    async def test_no_error_when_connected(self, handler, mock_connection, mock_app):
        """A healthy connection reports error=None."""
        mock_connection.database = "test_db"
        mock_connection.search_read.return_value = []

        result = await mock_app._tools["server_info_get"]()

        assert result.connected is True
        assert result.error is None
