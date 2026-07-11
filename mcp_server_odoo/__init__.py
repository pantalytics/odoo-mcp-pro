# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""MCP Server for Odoo - Model Context Protocol server for Odoo ERP systems."""

__version__ = "2.3.2"
__author__ = "Andrey Ivanov"
__license__ = "MPL-2.0"

from .access_control import AccessControlError, AccessController, ModelPermissions
from .config import OdooConfig, load_config
from .odoo_connection import OdooConnection, OdooConnectionError, create_connection
from .server import OdooMCPServer

__all__ = [
    "OdooMCPServer",
    "OdooConfig",
    "load_config",
    "OdooConnection",
    "OdooConnectionError",
    "create_connection",
    "AccessController",
    "AccessControlError",
    "ModelPermissions",
    "__version__",
]
