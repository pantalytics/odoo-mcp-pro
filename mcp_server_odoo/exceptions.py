# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""Shared exceptions for Odoo MCP server."""

from typing import Optional


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


class OdooDatabaseNotFoundError(OdooConnectionError):
    """The target Odoo database does not exist.

    A permanent condition: the database was never created or has been
    removed, so a retry against the same name fails the same way and each
    attempt makes Odoo log another "database does not exist". Distinct from
    OdooConnectionError, which also covers transient failures, so a caller
    with a retry policy can stop instead of re-dialing a removed database.
    Same intent as OdooTimeoutError.
    """

    pass


def is_missing_database_message(text: str) -> bool:
    """True when an error text reports that the Odoo database does not exist.

    Matches the psycopg2/Odoo signature (e.g. ``database "acme" does not
    exist``) from both the XML-RPC fault string and the JSON/2 error body.
    """
    if not text:
        return False
    lowered = text.lower()
    return "database" in lowered and "does not exist" in lowered


def raise_if_missing_database(
    text: str, database: Optional[str], cause: Optional[BaseException] = None
) -> None:
    """Raise OdooDatabaseNotFoundError when `text` reports a removed database.

    A no-op otherwise, so a call site keeps its normal error handling below.
    Every connection path that touches a database routes its failures through
    here, so the permanent-not-found message and exception type stay in one
    place.
    """
    if is_missing_database_message(text):
        raise OdooDatabaseNotFoundError(f"Odoo database '{database}' does not exist") from cause
