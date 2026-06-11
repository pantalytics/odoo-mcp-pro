"""MCP tool handlers for Odoo operations.

This module implements MCP tools for performing operations on Odoo data.
Tools are different from resources - they can have side effects and perform
actions like creating, updating, or deleting records.
"""

from __future__ import annotations

from typing import Optional, Tuple

from mcp.server.fastmcp import FastMCP

from ..access_control import AccessController
from ..config import OdooConfig
from ..connection_protocol import OdooConnectionProtocol
from ..error_handling import ValidationError
from ._common import _current_sub, logger
from .binary import BinaryToolsMixin
from .bulk import BulkToolsMixin
from .crud import CrudToolsMixin
from .formatting import FormattingMixin
from .introspection import IntrospectionToolsMixin
from .messaging import MessagingToolsMixin
from .query import QueryToolsMixin


class OdooToolHandler(
    FormattingMixin,
    QueryToolsMixin,
    IntrospectionToolsMixin,
    CrudToolsMixin,
    BulkToolsMixin,
    BinaryToolsMixin,
    MessagingToolsMixin,
):
    """Handles MCP tool requests for Odoo operations."""

    def __init__(
        self,
        app: FastMCP,
        connection: Optional[OdooConnectionProtocol] = None,
        access_controller: Optional[AccessController] = None,
        config: Optional[OdooConfig] = None,
    ):
        """Initialize tool handler with a direct Odoo connection."""
        self.app = app
        self.connection = connection
        self.access_controller = access_controller
        self.config = config

        # Register tools
        self._register_tools()

    async def _get_user_context(
        self,
    ) -> Tuple[OdooConnectionProtocol, AccessController, str]:
        """Get connection and access controller for the current request.

        Admin extension hook: the private SaaS package overrides this to
        resolve a per-user connection from the authenticated subject.

        Returns:
            Tuple of (connection, access_controller, sub)

        Raises:
            ValidationError: If no connection is available
        """
        if self.connection is not None and self.access_controller is not None:
            _current_sub.set("stdio")
            return self.connection, self.access_controller, "stdio"

        raise ValidationError("No Odoo connection available")

    def _track_usage(self, sub: str, tool_name: str) -> None:
        """Usage tracking hook. No-op here; overridden by the SaaS layer."""

    def _register_tools(self):
        """Register all tool handlers with FastMCP.

        Registration order matters: tools appear to clients in the order
        they are registered, so this must match the original sequence.
        """
        self._register_query_tools()
        self._register_introspection_tools()
        self._register_crud_tools()
        self._register_bulk_tools()
        self._register_binary_tools()
        self._register_messaging_tools()


def register_tools(
    app: FastMCP,
    connection: Optional[OdooConnectionProtocol] = None,
    access_controller: Optional[AccessController] = None,
    config: Optional[OdooConfig] = None,
) -> OdooToolHandler:
    """Register all Odoo tools with the FastMCP app.

    Args:
        app: FastMCP application instance
        connection: Odoo connection instance
        access_controller: Access control instance
        config: Odoo configuration instance

    Returns:
        The tool handler instance
    """
    handler = OdooToolHandler(
        app,
        connection=connection,
        access_controller=access_controller,
        config=config,
    )
    logger.info("Registered Odoo MCP tools")
    return handler
