# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""Shared exceptions for Odoo MCP server."""


class OdooConnectionError(Exception):
    """Exception raised when connection to Odoo fails."""

    pass


class OdooTimeoutError(OdooConnectionError):
    """A request to the Odoo server timed out.

    Distinct from OdooConnectionError so callers that probe repeatedly
    (e.g. the per-operation permission checks) can stop after the first
    timeout instead of paying the full timeout once per probe.
    """

    pass
