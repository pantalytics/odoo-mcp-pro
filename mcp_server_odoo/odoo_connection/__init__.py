# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
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
