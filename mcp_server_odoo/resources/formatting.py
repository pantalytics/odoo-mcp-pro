# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""Parsing and formatting helpers for Odoo MCP resources."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from ..connection_protocol import OdooConnectionProtocol
from ..formatters import DatasetFormatter, RecordFormatter
from ..logging_config import get_logger
from ..uri_schema import (
    build_search_uri,
)

logger = get_logger(__name__)


class ResourceFormattingMixin:
    """Parameter parsing and result formatting for resource handlers."""

    def _parse_domain(self, domain: Optional[str]) -> List[Any]:
        """Parse domain parameter from URL-encoded string.

        Args:
            domain: URL-encoded domain string

        Returns:
            Parsed domain list
        """
        if not domain:
            return []

        try:
            # URL decode
            decoded = unquote(domain)
            # Parse JSON
            parsed = json.loads(decoded)

            if not isinstance(parsed, list):
                raise ValueError("Domain must be a list")

            return parsed
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Invalid domain parameter: {domain} - {e}")
            return []

    def _parse_fields(self, fields: Optional[str]) -> Optional[List[str]]:
        """Parse fields parameter from comma-separated string.

        Args:
            fields: Comma-separated field names

        Returns:
            List of field names or None
        """
        if not fields:
            return None

        # Split and clean field names
        field_list = [f.strip() for f in fields.split(",") if f.strip()]
        return field_list if field_list else None

    def _parse_limit(self, limit: Optional[int]) -> int:
        """Parse and validate limit parameter.

        Args:
            limit: Limit value from request

        Returns:
            Valid limit value
        """
        if limit is None:
            return self.config.default_limit

        # Ensure it's within bounds
        if limit <= 0:
            return self.config.default_limit
        elif limit > self.config.max_limit:
            return self.config.max_limit
        else:
            return limit

    def _parse_offset(self, offset: Optional[int]) -> int:
        """Parse and validate offset parameter.

        Args:
            offset: Offset value from request

        Returns:
            Valid offset value
        """
        if offset is None or offset < 0:
            return 0
        return offset

    def _parse_order(self, order: Optional[str]) -> Optional[str]:
        """Parse and validate order parameter.

        Args:
            order: Order string (e.g., "name asc, id desc")

        Returns:
            Validated order string or None
        """
        if not order:
            return None

        # Basic validation - just ensure it's not empty after stripping
        cleaned = order.strip()
        return cleaned if cleaned else None

    def _format_search_results(
        self,
        model: str,
        records: List[Dict[str, Any]],
        domain: List[Any],
        fields: Optional[List[str]],
        limit: int,
        offset: int,
        total_count: int,
        fields_metadata: Optional[Dict[str, Any]],
    ) -> str:
        """Format search results with pagination metadata.

        Args:
            model: Model name
            records: List of record data
            domain: Applied domain filter
            fields: Requested fields
            limit: Records per page
            offset: Current offset
            total_count: Total matching records
            fields_metadata: Field metadata for formatting

        Returns:
            Formatted search results
        """
        # Calculate pagination info
        current_page = (offset // limit) + 1 if limit > 0 else 1
        total_pages = (total_count + limit - 1) // limit if limit > 0 else 1
        has_next = offset + limit < total_count
        has_prev = offset > 0

        # Build pagination URIs
        next_uri = None
        prev_uri = None

        if has_next:
            # Convert domain back to JSON string for URI
            domain_str = json.dumps(domain) if domain else None
            fields_str = ",".join(fields) if fields else None
            next_uri = build_search_uri(
                model, domain=domain_str, fields=fields_str, limit=limit, offset=offset + limit
            )

        if has_prev:
            prev_offset = max(0, offset - limit)
            # Convert domain back to JSON string for URI
            domain_str = json.dumps(domain) if domain else None
            fields_str = ",".join(fields) if fields else None
            prev_uri = build_search_uri(
                model, domain=domain_str, fields=fields_str, limit=limit, offset=prev_offset
            )

        # Use DatasetFormatter for rich formatting
        formatter = DatasetFormatter(model)
        return formatter.format_search_results(
            records=records,
            total_count=total_count,
            limit=limit,
            offset=offset,
            domain=domain,
            fields=fields,
            fields_metadata=fields_metadata,
            next_uri=next_uri,
            prev_uri=prev_uri,
            current_page=current_page,
            total_pages=total_pages,
        )

    def _format_count_result(self, model: str, count: int, domain: List[Any]) -> str:
        """Format count result.

        Args:
            model: Model name
            count: Record count
            domain: Applied domain filter

        Returns:
            Formatted count result
        """
        lines = [
            f"{'=' * 60}",
            f"Count Result: {model}",
            f"{'=' * 60}",
        ]

        if domain:
            formatter = DatasetFormatter(model)
            lines.append(f"Search criteria: {formatter._format_domain(domain)}")
        else:
            lines.append("Search criteria: All records")

        lines.append("")
        lines.append(f"Total count: {count:,} record(s)")

        return "\n".join(lines)

    def _format_fields_result(self, model: str, fields: Dict[str, Dict[str, Any]]) -> str:
        """Format field definitions result.

        Args:
            model: Model name
            fields: Field definitions dictionary

        Returns:
            Formatted field definitions
        """
        lines = [
            f"{'=' * 60}",
            f"Field Definitions: {model}",
            f"{'=' * 60}",
            f"Total fields: {len(fields)}",
            "",
        ]

        # Group fields by type
        fields_by_type = {}
        for field_name, field_info in sorted(fields.items()):
            field_type = field_info.get("type", "unknown")
            if field_type not in fields_by_type:
                fields_by_type[field_type] = []
            fields_by_type[field_type].append((field_name, field_info))

        # Format fields by type
        for field_type in sorted(fields_by_type.keys()):
            lines.append(f"\n{field_type.upper()} Fields ({len(fields_by_type[field_type])}):")
            lines.append("-" * 30)

            for field_name, field_info in fields_by_type[field_type]:
                lines.append(f"\n{field_name}:")
                lines.append(f"  Label: {field_info.get('string', 'N/A')}")
                lines.append(f"  Required: {field_info.get('required', False)}")
                lines.append(f"  Readonly: {field_info.get('readonly', False)}")

                # Add type-specific information
                if field_type == "selection":
                    selection = field_info.get("selection", [])
                    if selection and len(selection) <= 5:
                        lines.append(
                            f"  Options: {', '.join([f'{k} ({v})' for k, v in selection])}"
                        )
                    elif selection:
                        lines.append(f"  Options: {len(selection)} choices available")

                elif field_type in ("many2one", "one2many", "many2many"):
                    relation = field_info.get("relation", "N/A")
                    lines.append(f"  Related Model: {relation}")

                elif field_type in ("float", "monetary"):
                    digits = field_info.get("digits", "N/A")
                    lines.append(f"  Precision: {digits}")

                # Add help text if available
                help_text = field_info.get("help", "")
                if help_text:
                    lines.append(
                        f"  Help: {help_text[:100]}{'...' if len(help_text) > 100 else ''}"
                    )

        return "\n".join(lines)

    def _format_record(
        self,
        model: str,
        record: Dict[str, Any],
        connection: Optional[OdooConnectionProtocol] = None,
    ) -> str:
        """Format a record for MCP consumption.

        Args:
            model: The model name
            record: The record data
            connection: Odoo connection to use for field metadata

        Returns:
            Formatted text representation
        """
        conn = connection or self.connection
        # Get field metadata if available
        try:
            fields_metadata = conn.fields_get(model) if conn else None
        except Exception as e:
            logger.debug(f"Could not retrieve field metadata: {e}")
            fields_metadata = None

        # Use RecordFormatter for rich formatting
        formatter = RecordFormatter(model)
        return formatter.format_record(record, fields_metadata)
