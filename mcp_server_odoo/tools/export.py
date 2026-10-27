"""Compact export MCP tool: record_export.

Odoo's own export (`export_data`, the engine behind the Export dialog) turns
records into rows, so a large dataset comes back as one CSV text: the header
once, then one line per row, instead of a JSON object per record.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional

from mcp.types import ToolAnnotations

from ..access_control import AccessControlError
from ..error_handling import ValidationError
from ..error_sanitizer import ErrorSanitizer
from ..logging_config import perf_logger
from ..odoo_connection import OdooConnectionError
from ..schemas import ExportResult
from ._common import (
    _current_sub,
    logger,
    odoo_error_as_validation,
    parse_domain,
    run_blocking,
    validate_access,
)
from .field_hints import domain_field_names, unknown_fields_hint


def rows_to_csv(header: List[str], rows: List[List[Any]]) -> str:
    """Write rows as CSV with the cell rules of Odoo's CSV export.

    False and None become empty cells. A text cell that starts with =, + or -
    gets a leading quote, so a spreadsheet does not run it as a formula.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        cells = []
        for value in row:
            if value is None or value is False:
                value = ""
            elif isinstance(value, str) and value.startswith(("=", "+", "-")):
                value = "'" + value
            cells.append(value)
        writer.writerow(cells)
    return buffer.getvalue()


class ExportToolsMixin:
    """record_export tool."""

    def _register_export_tools(self):
        """Register the record_export tool with the MCP server."""

        @self.tool(
            title="Export Records (compact CSV)",
            annotations=ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=True,
            ),
        )
        async def record_export(
            model: str,
            fields: List[str],
            domain: Optional[Any] = None,
            order: Optional[str] = None,
            limit: Optional[int] = None,
            offset: int = 0,
            connection: Optional[str] = None,
        ) -> ExportResult:
            """Export many records as compact CSV text (Odoo's Export feature).

            Use this for a large list of rows (thousands of order lines,
            journal items, stock quants) that the user wants in a spreadsheet
            or file, or that you must check row by row. The header comes once,
            so each row is much smaller than in record_search. For totals per
            group, use record_aggregate instead. Write the csv text to a file
            when the user wants a file.

            Args:
                model: The Odoo model name, e.g. 'sale.order.line'.
                fields: Columns to export. Use '/' to follow a relation, as in
                    Odoo's Export dialog: ['order_id/name', 'product_id/default_code',
                    'order_id/partner_shipping_id/city', 'product_uom_qty'].
                    A relation without a subfield gives its display name.
                    'id' (or '.id') is the database id, 'partner_id/id' the
                    partner's. External ids are not exported: Odoo creates
                    missing ones during export, which writes to the database.
                domain: Odoo domain filter, same format as record_search.
                order: Sort order, e.g. 'date_order desc, id'. Default 'id',
                    so pages do not overlap.
                limit: Records per page. Default and maximum: the server's
                    record limit; a larger value gives the maximum.
                offset: Number of records to skip, for the next page.
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info_get's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                `csv` with a header line and one line per row. Values are in
                Odoo's export format: selection labels, display names, dates as
                YYYY-MM-DD, datetimes in UTC. A one2many subfield path (e.g.
                'order_line/product_id') adds one row per line, so the csv can
                hold more rows than `records`. `has_more` tells you whether to
                page on.
            """
            result = await self._handle_export_tool(
                model, fields, domain, order, limit, offset, connection
            )
            self._track_usage(_current_sub.get(), "record_export")
            return ExportResult(**result)

    async def _handle_export_tool(
        self,
        model: str,
        fields: List[str],
        domain: Optional[Any],
        order: Optional[str],
        limit: Optional[int],
        offset: int,
        connection_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle record_export tool request."""
        if not fields:
            raise ValidationError(
                "fields must name at least one column, e.g. ['name', 'partner_id/name']."
            )
        paths = []
        for path in fields:
            if not path.strip():
                raise ValidationError(f"Invalid field path: {path!r}")
            # 'partner_id:id' is Odoo's own external id syntax: refused.
            if "id" in re.split(":", path)[1:]:
                raise ValidationError(
                    f"Field path {path!r} exports an external id, and Odoo creates "
                    "missing external ids during export. Use '.id' for the database "
                    "id, or 'name' for the display name."
                )
            # 'id' and 'partner_id/id' mean the database id here, the same id
            # record_search returns. To Odoo they are external ids, which
            # export would create, so they go to Odoo as '.id'.
            paths.append("/".join(".id" if part == "id" else part for part in path.split("/")))
        if limit is None or limit <= 0:
            limit = self.config.max_limit
        limit = min(limit, self.config.max_limit)
        if offset < 0:
            raise ValidationError("offset must not be negative")
        parsed_domain = parse_domain(domain)
        connection = None

        try:
            connection, access_controller, sub = await self._get_user_context(connection_selector)
            with perf_logger.track_operation("tool_export", model=model):
                await validate_access(connection, access_controller, model, "read")

                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                record_ids = await run_blocking(
                    connection,
                    connection.search,
                    model,
                    parsed_domain,
                    # One extra id tells us whether another page exists.
                    limit=limit + 1,
                    offset=offset,
                    order=order or "id",
                )
                has_more = len(record_ids) > limit
                record_ids = record_ids[:limit]
                rows: List[List[Any]] = []
                if record_ids:
                    exported = await run_blocking(
                        connection,
                        connection.call_method,
                        model,
                        "export_data",
                        ids=record_ids,
                        fields_to_export=paths,
                    )
                    rows = exported["datas"]

                return {
                    "model": model,
                    "csv": rows_to_csv(fields, rows),
                    "records": len(record_ids),
                    "limit": limit,
                    "offset": offset,
                    "has_more": has_more,
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"AccessError: {e}") from e
        except OdooConnectionError as e:
            names = domain_field_names(parsed_domain) + [
                path.split("/", 1)[0] for path in paths if not path.startswith(".")
            ]
            hint = await unknown_fields_hint(connection, model, e, names)
            raise (hint or odoo_error_as_validation(e)) from e
        except Exception as e:
            logger.error(f"Error in record_export tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Export failed: {sanitized_msg}") from e
