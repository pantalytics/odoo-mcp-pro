"""Odoo XML-RPC connection management.

This package provides the OdooConnection class for managing connections
to Odoo via XML-RPC using MCP-specific endpoints.
"""

from ..exceptions import OdooConnectionError
from .core import OdooConnection, create_connection

__all__ = [
    "OdooConnection",
    "OdooConnectionError",
    "create_connection",
]
