# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""Resource handler class and registration for Odoo MCP resources."""

from __future__ import annotations

from typing import Optional, Tuple

from mcp.server.fastmcp import FastMCP
from mcp.types import Annotations

from ..access_control import AccessController
from ..config import OdooConfig
from ..connection_protocol import OdooConnectionProtocol
from ..error_handling import ValidationError
from ..logging_config import get_logger
from .formatting import ResourceFormattingMixin
from .retrieval import RetrievalMixin

logger = get_logger(__name__)


class OdooResourceHandler(RetrievalMixin, ResourceFormattingMixin):
    """Handles MCP resource requests for Odoo data."""

    def __init__(
        self,
        app: FastMCP,
        connection: Optional[OdooConnectionProtocol] = None,
        access_controller: Optional[AccessController] = None,
        config: Optional[OdooConfig] = None,
    ):
        """Initialize resource handler with a direct Odoo connection."""
        self.app = app
        self.connection = connection
        self.access_controller = access_controller
        self.config = config

        # Register resources
        self._register_resources()

    async def _get_user_context(self) -> Tuple[OdooConnectionProtocol, AccessController]:
        """Get connection and access controller for the current request.

        Admin extension hook: the private SaaS package overrides this to
        resolve a per-user connection from the authenticated subject.

        Returns:
            Tuple of (connection, access_controller)

        Raises:
            ValidationError: If no connection is available
        """
        if self.connection is not None and self.access_controller is not None:
            return self.connection, self.access_controller

        raise ValidationError("No Odoo connection available")

    def _register_resources(self):
        """Register all resource handlers with FastMCP."""
        # Note: FastMCP uses decorators to register resources.
        # The @self.app.resource decorator automatically handles resource registration.
        # Resources with parameters (like {model}) are registered as templates,
        # not concrete resources, so they won't show in list_resources().

        # Add some concrete resources for enabled models
        # These will show up in the resource list
        self._register_concrete_resources()

        # Register record retrieval resource handler
        @self.app.resource(
            "odoo://{model}/record/{record_id}",
            title="Odoo Record",
            description="Retrieve a specific record from an Odoo model by ID",
            annotations=Annotations(audience=["assistant"], priority=0.5),
        )
        async def get_record(model: str, record_id: str) -> str:
            """Retrieve a specific record from Odoo.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_id: The record ID to retrieve

            Returns:
                Formatted record data as text
            """
            return await self._handle_record_retrieval(model, record_id)

        # Register search resource (no parameters due to FastMCP limitations)
        @self.app.resource(
            "odoo://{model}/search",
            title="Odoo Search",
            description="Search records with default settings (first 10 records)",
            annotations=Annotations(audience=["assistant"], priority=0.5),
        )
        async def search_records(model: str) -> str:
            """Search records with default settings.

            Returns first 10 records with all fields.
            For more control, use the search_records tool instead.
            """
            return await self._handle_search(model, None, None, None, None, None)

        # Note: Browse resource removed due to FastMCP query parameter limitations
        # Use get_record multiple times or search_records tool instead

        # Register count resource (no parameters due to FastMCP limitations)
        @self.app.resource(
            "odoo://{model}/count",
            title="Odoo Record Count",
            description="Count all records in an Odoo model",
            annotations=Annotations(audience=["assistant"], priority=0.3),
        )
        async def count_records(model: str) -> str:
            """Count all records in the model.

            For filtered counts, use the search_records tool with limit=0.
            """
            return await self._handle_count(model, None)

        # Register fields resource
        @self.app.resource(
            "odoo://{model}/fields",
            title="Odoo Field Definitions",
            description="Get field definitions and metadata for an Odoo model",
            annotations=Annotations(audience=["assistant"], priority=0.4),
        )
        async def get_fields(model: str) -> str:
            """Get field definitions for a model.

            Args:
                model: The Odoo model name (e.g., 'res.partner')

            Returns:
                Formatted field definitions and metadata
            """
            return await self._handle_fields(model)

    def _register_concrete_resources(self):
        """Register concrete resources for enabled models.

        Note: In the current FastMCP implementation, resources with parameters
        are registered as templates and won't show in list_resources().
        This is expected behavior - use list_resource_templates() to see them.
        """
        # The template resources registered with decorators are sufficient
        # FastMCP will handle them properly as templates
        pass


def register_resources(
    app: FastMCP,
    connection: Optional[OdooConnectionProtocol] = None,
    access_controller: Optional[AccessController] = None,
    config: Optional[OdooConfig] = None,
) -> OdooResourceHandler:
    """Register all Odoo resources with the FastMCP app.

    Args:
        app: FastMCP application instance
        connection: Odoo connection instance
        access_controller: Access control instance
        config: Odoo configuration instance

    Returns:
        The resource handler instance
    """
    handler = OdooResourceHandler(
        app,
        connection=connection,
        access_controller=access_controller,
        config=config,
    )
    logger.info("Registered Odoo MCP resources")
    return handler
