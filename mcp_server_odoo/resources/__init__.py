# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""MCP resource handlers for Odoo data access.

This package implements MCP resources for accessing Odoo data through
standardized URIs using FastMCP decorators.
"""

from .handler import OdooResourceHandler, register_resources

__all__ = ["OdooResourceHandler", "register_resources"]
