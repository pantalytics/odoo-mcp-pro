"""Compact export MCP tool: export_records.

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

# Odoo also accepts 'partner_id:id' for 'partner_id/id'.
_COLON_ID_RE = re.compile(r"([^/]):id")


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
    """export_records tool."""

    def _register_export_tools(self):
        """Register the export_records tool with the MCP server."""

        @self.tool(
            title="Export Records (compact CSV)",
            annotations=ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=True,
            ),
        )
        async def export_records(
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
            so a page holds far more rows than search_records. For totals per
            group, use aggregate_records instead. Write the csv text to a file
            when the user wants a file.

            Args:
                model: The Odoo model name, e.g. 'sale.order.line'.
                fields: Columns to export. Use '/' to follow a relation, as in
                    Odoo's Export dialog: ['order_id/name', 'product_id/default_code',
                    'order_id/partner_shipping_id/city', 'product_uom_qty'].
                    A relation without a subfield gives its display name. Use
                    '.id' for the database id. External id paths ('id',
                    'partner_id/id') are refused: Odoo creates missing external
                    ids during export, which writes to the database.
                domain: Odoo domain filter, same format as search_records.
                order: Sort order, e.g. 'date_order desc, id'. Default 'id',
                    so pages do not overlap.
                limit: Records per page. Default and maximum: the server's
                    record limit.
                offset: Number of records to skip, for the next page.
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                `csv` with a header line and one line per row. Values are in
                Odoo's export format: selection labels, display names, dates as
                YYYY-MM-DD, datetimes in UTC. A one2many subfield path (e.g.
                'order_line/product_id') adds one row per line, so `rows` can
                be more than `records`. `total` and `has_more` tell you whether
                to page on.
            """
            result = await self._handle_export_tool(
                model, fields, domain, order, limit, offset, connection
            )
            self._track_usage(_current_sub.get(), "export_records")
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
        """Handle export_records tool request."""
        if not fields:
            raise ValidationError(
                "fields must name at least one column, e.g. ['name', 'partner_id/name']."
            )
        for path in fields:
            if not path.strip():
                raise ValidationError(f"Invalid field path: {path!r}")
            if "id" in _COLON_ID_RE.sub(r"\1/id", path).split("/"):
                raise ValidationError(
                    f"Field path {path!r} exports an external id, and Odoo creates "
                    "missing external ids during export. Use '.id' for the database "
                    "id, or 'name' for the display name."
                )
        if limit is None:
            limit = self.config.max_limit
        if limit <= 0 or limit > self.config.max_limit:
            raise ValidationError(f"limit must be between 1 and {self.config.max_limit}")
        if offset < 0:
            raise ValidationError("offset must not be negative")
        parsed_domain = parse_domain(domain)

        try:
            connection, access_controller, sub = await self._get_user_context(connection_selector)
            with perf_logger.track_operation("tool_export", model=model):
                await validate_access(connection, access_controller, model, "read")

                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                total = await run_blocking(
                    connection, connection.search_count, model, parsed_domain
                )
                record_ids = await run_blocking(
                    connection,
                    connection.search,
                    model,
                    parsed_domain,
                    limit=limit,
                    offset=offset,
                    order=order or "id",
                )
                rows: List[List[Any]] = []
                if record_ids:
                    exported = await run_blocking(
                        connection,
                        connection.call_method,
                        model,
                        "export_data",
                        ids=record_ids,
                        fields_to_export=list(fields),
                    )
                    rows = exported["datas"]

                return {
                    "model": model,
                    "fields": list(fields),
                    "csv": rows_to_csv(list(fields), rows),
                    "records": len(record_ids),
                    "rows": len(rows),
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": offset + len(record_ids) < total,
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"AccessError: {e}") from e
        except OdooConnectionError as e:
            raise odoo_error_as_validation(e) from e
        except Exception as e:
            logger.error(f"Error in export_records tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Export failed: {sanitized_msg}") from e
