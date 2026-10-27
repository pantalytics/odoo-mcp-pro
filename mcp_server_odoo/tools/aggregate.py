"""Grouped aggregation MCP tool: record_aggregate.

Odoo groups and sums the records itself (`read_group` /
`formatted_read_group`), so a report over many thousands of lines comes back as
a few compact rows instead of pages of raw records.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from mcp.types import ToolAnnotations

from ..access_control import AccessControlError
from ..error_handling import ValidationError
from ..error_sanitizer import ErrorSanitizer
from ..logging_config import perf_logger
from ..odoo_connection import OdooConnectionError
from ..schemas import AggregateResult
from ._common import (
    _current_sub,
    logger,
    odoo_error_as_validation,
    parse_domain,
    run_blocking,
    validate_access,
)
from .field_hints import domain_field_names, unknown_fields_hint

MAX_GROUPBY = 3
# array_agg / recordset are left out: they return id lists, not compact totals.
AGGREGATE_FUNCTIONS = ("sum", "avg", "min", "max", "count", "count_distinct", "bool_and", "bool_or")
_AGGREGATE_RE = re.compile(rf"^\w+:({'|'.join(AGGREGATE_FUNCTIONS)})$")


def normalize_order(
    order: Optional[str], groupby: List[str], aggregates: List[str]
) -> Optional[str]:
    """Rewrite order terms Odoo would refuse into the spec they can only mean.

    Odoo sorts groups only by a groupby or aggregate spec. A bare field that
    names exactly one of them ('amount_total desc' with 'amount_total:sum',
    'date' with 'date:month') becomes that spec, and 'count' becomes
    '__count'. Any other term is left for Odoo to judge.
    """
    if not order:
        return order
    specs = list(groupby) + list(aggregates) + ["__count"]
    terms = []
    for term in order.split(","):
        key, *direction = term.split()
        if key not in specs:
            if key == "count":
                key = "__count"
            else:
                matches = [spec for spec in specs if spec.split(":", 1)[0] == key]
                if len(matches) == 1:
                    key = matches[0]
        terms.append(" ".join([key, *direction]))
    return ", ".join(terms)


class AggregateToolsMixin:
    """record_aggregate tool."""

    def _register_aggregate_tools(self):
        """Register the record_aggregate tool with the MCP server."""

        @self.tool(
            title="Aggregate Records (grouped totals)",
            annotations=ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=True,
            ),
        )
        async def record_aggregate(
            model: str,
            groupby: Optional[List[str]] = None,
            aggregates: Optional[List[str]] = None,
            domain: Optional[Any] = None,
            order: Optional[str] = None,
            limit: int = 100,
            offset: int = 0,
            connection: Optional[str] = None,
        ) -> AggregateResult:
            """Group records and compute totals in Odoo (read_group).

            Use this for reports instead of paging through records: revenue per
            product per month, quantity per location, invoice count per
            customer. Odoo does the math; you get one compact row per group.

            Args:
                model: The Odoo model name, e.g. 'account.move.line'.
                groupby: Up to 3 fields to group by. Use 'field:granularity'
                    for dates (day, week, month, quarter, year), e.g.
                    ['product_id', 'date:month']. Omit for one grand-total row.
                aggregates: Totals per group as 'field:function', e.g.
                    ['price_subtotal:sum', 'quantity:sum', 'partner_id:count_distinct'].
                    Functions: sum, avg, min, max, count, count_distinct,
                    bool_and, bool_or. The record count ('__count') is always
                    included.
                domain: Odoo domain filter, same format as record_search.
                order: Sort groups by a groupby or aggregate spec, e.g.
                    'price_subtotal:sum desc' for a top-N list.
                limit: Maximum groups to return. Above the server's record limit
                    you get the limit; `limit` in the result says which.
                offset: Number of groups to skip, for the next page.
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info_get's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                One row per group. Many2one groups come as [id, name], date
                groups as [range start, label]; `has_more` tells you whether to
                page on.
            """
            result = await self._handle_aggregate_tool(
                model, groupby, aggregates, domain, order, limit, offset, connection
            )
            self._track_usage(_current_sub.get(), "record_aggregate")
            return AggregateResult(**result)

    async def _handle_aggregate_tool(
        self,
        model: str,
        groupby: Optional[List[str]],
        aggregates: Optional[List[str]],
        domain: Optional[Any],
        order: Optional[str],
        limit: int,
        offset: int,
        connection_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle record_aggregate tool request."""
        groupby = list(groupby or [])
        aggregates = [spec for spec in (aggregates or []) if spec != "__count"]

        if len(groupby) > MAX_GROUPBY:
            raise ValidationError(
                f"Group by at most {MAX_GROUPBY} fields, got {len(groupby)}. "
                "Filter with domain or split the report into several calls."
            )
        for spec in groupby:
            if not spec.strip():
                raise ValidationError(f"Invalid groupby spec: {spec!r}")
        for spec in aggregates:
            if not _AGGREGATE_RE.match(spec):
                raise ValidationError(
                    f"Invalid aggregate {spec!r}. Use 'field:function' with one of: "
                    f"{', '.join(AGGREGATE_FUNCTIONS)}."
                )
        # Same as record_search: no limit means the default, over the cap
        # means the cap. The result's `limit` says what was used.
        if limit <= 0:
            limit = self.config.default_limit
        limit = min(limit, self.config.max_limit)
        if offset < 0:
            raise ValidationError("offset must not be negative")
        parsed_domain = parse_domain(domain)
        order = normalize_order(order, groupby, aggregates)
        connection = None

        try:
            connection, access_controller, sub = await self._get_user_context(connection_selector)
            with perf_logger.track_operation("tool_aggregate", model=model):
                await validate_access(connection, access_controller, model, "read")

                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # One extra group tells us whether another page exists.
                groups = await run_blocking(
                    connection,
                    connection.read_group,
                    model,
                    parsed_domain,
                    groupby,
                    aggregates,
                    order=order,
                    offset=offset,
                    limit=limit + 1,
                )

                return {
                    "model": model,
                    "groups": groups[:limit],
                    "limit": limit,
                    "offset": offset,
                    "has_more": len(groups) > limit,
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"AccessError: {e}") from e
        except OdooConnectionError as e:
            names = domain_field_names(parsed_domain) + [
                spec.split(":", 1)[0] for spec in groupby + aggregates
            ]
            hint = await unknown_fields_hint(connection, model, e, names)
            raise (hint or odoo_error_as_validation(e)) from e
        except Exception as e:
            logger.error(f"Error in record_aggregate tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Aggregation failed: {sanitized_msg}") from e
