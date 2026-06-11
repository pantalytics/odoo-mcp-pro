"""MCP tool handlers for Odoo operations.

This package implements MCP tools for performing operations on Odoo data.
Tools are different from resources - they can have side effects and perform
actions like creating, updating, or deleting records.

Public API is re-exported here so existing imports keep working:
``from mcp_server_odoo.tools import OdooToolHandler, register_tools, ...``
"""

from ..error_handling import ValidationError
from ._common import (
    _AVATAR_FIELD_RE,
    _BINARY_UPLOAD_SEMAPHORE,
    MAX_BINARY_SIZE_BYTES,
    MAX_BULK_SIZE,
    MAX_CONCURRENT_BINARY_UPLOADS,
    _current_sub,
    logger,
)
from .handler import OdooToolHandler, register_tools

__all__ = [
    "MAX_BINARY_SIZE_BYTES",
    "MAX_BULK_SIZE",
    "MAX_CONCURRENT_BINARY_UPLOADS",
    "OdooToolHandler",
    "ValidationError",
    "register_tools",
    "_AVATAR_FIELD_RE",
    "_BINARY_UPLOAD_SEMAPHORE",
    "_current_sub",
    "logger",
]
