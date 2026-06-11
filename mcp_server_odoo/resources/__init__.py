"""MCP resource handlers for Odoo data access.

This package implements MCP resources for accessing Odoo data through
standardized URIs using FastMCP decorators.
"""

from .handler import OdooResourceHandler, register_resources

__all__ = ["OdooResourceHandler", "register_resources"]
