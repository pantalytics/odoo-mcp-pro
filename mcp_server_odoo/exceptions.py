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


class OdooExecutionError(OdooConnectionError):
    """Odoo received the request and refused it.

    This is a fault the Odoo server returned *after* the request arrived --
    an invalid field in a domain, a method or model that does not exist, a
    denied permission, or a business-rule (UserError/ValidationError)
    violation. The transport worked; the request itself is the problem.

    Kept a subclass of OdooConnectionError so callers that already catch
    OdooConnectionError keep working, but named distinctly so the failure is
    no longer counted as a connection outage and so it is never retried:
    retrying a request Odoo already rejected cannot succeed, and for a write
    that partly committed it is unsafe. A bare OdooConnectionError means the
    transport failed (the request may never have reached Odoo) and is the
    only kind a caller may retry.
    """

    pass
