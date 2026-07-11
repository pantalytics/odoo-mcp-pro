# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""URI schema module for Odoo MCP Server.

This module implements the URI schema for accessing Odoo data through MCP resources.
It provides parsing, validation, and building utilities for Odoo URIs following the
pattern: odoo://{model}/{operation}?{parameters}

Supported operations:
- record/{id}: Fetch a specific record
- search: Search for records using domain
- browse: Retrieve multiple records by IDs
- count: Count matching records
- fields: Get field definitions
"""

import re
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class OdooOperation(Enum):
    """Supported Odoo URI operations."""

    RECORD = "record"
    SEARCH = "search"
    BROWSE = "browse"
    COUNT = "count"
    FIELDS = "fields"


@dataclass
class OdooURI:
    """Parsed Odoo URI representation."""

    model: str
    operation: OdooOperation
    record_id: Optional[int] = None
    domain: Optional[str] = None
    fields: Optional[List[str]] = None
    limit: Optional[int] = None
    offset: Optional[int] = None
    order: Optional[str] = None
    ids: Optional[List[int]] = None

    def to_uri(self) -> str:
        """Convert the parsed URI back to string format."""
        return build_uri(
            model=self.model,
            operation=self.operation.value,
            record_id=self.record_id,
            domain=self.domain,
            fields=self.fields,
            limit=self.limit,
            offset=self.offset,
            order=self.order,
            ids=self.ids,
        )


class URIError(Exception):
    """Base exception for URI-related errors."""

    pass


class URIParseError(URIError):
    """Exception raised when URI parsing fails."""

    pass


class URIValidationError(URIError):
    """Exception raised when URI validation fails."""

    pass


# URI pattern for matching odoo:// URIs
# This pattern is permissive in what it captures, validation happens later
# It requires at least one non-slash character for the model name
URI_PATTERN = re.compile(r"^odoo://([^/]+)/([^/?]+)(?:/(\d+))?(?:\?(.*))?$")


def parse_uri(uri: str) -> OdooURI:
    """Parse an Odoo URI string into its components.

    Args:
        uri: The URI string to parse (e.g., "odoo://res.partner/search?domain=[('is_company','=',True)]")

    Returns:
        OdooURI object with parsed components

    Raises:
        URIParseError: If the URI format is invalid
        URIValidationError: If the URI components are invalid
    """
    if not uri.startswith("odoo://"):
        raise URIParseError(f"URI must start with 'odoo://', got: {uri}")

    match = URI_PATTERN.match(uri)
    if not match:
        raise URIParseError(f"Invalid URI format: {uri}")

    model, operation_str, record_id_str, query_string = match.groups()

    # Validate model name (check for empty model first)
    if not model:
        raise URIValidationError("Invalid model name: empty model name")
    if not _is_valid_model_name(model):
        raise URIValidationError(f"Invalid model name: {model}")

    # Parse operation and record ID
    try:
        if operation_str == "record" and record_id_str:
            operation = OdooOperation.RECORD
            record_id = int(record_id_str)
        elif operation_str in [op.value for op in OdooOperation]:
            operation = OdooOperation(operation_str)
            record_id = None
        else:
            raise URIValidationError(f"Invalid operation: {operation_str}")
    except ValueError:
        raise URIValidationError(f"Invalid record ID: {record_id_str}") from None

    # Validate operation-specific requirements
    if operation == OdooOperation.RECORD and not record_id:
        raise URIValidationError("Record operation requires an ID")

    # Parse query parameters
    params = _parse_query_parameters(query_string) if query_string else {}

    # Extract and validate parameters
    domain = params.get("domain")
    fields = _parse_fields_parameter(params.get("fields"))
    limit = _parse_int_parameter(params.get("limit"), "limit")
    offset = _parse_int_parameter(params.get("offset"), "offset")
    order = params.get("order")
    ids = _parse_ids_parameter(params.get("ids"))

    # Validate operation-specific parameters
    if operation == OdooOperation.BROWSE and not ids:
        raise URIValidationError("Browse operation requires 'ids' parameter")

    return OdooURI(
        model=model,
        operation=operation,
        record_id=record_id,
        domain=domain,
        fields=fields,
        limit=limit,
        offset=offset,
        order=order,
        ids=ids,
    )


def build_uri(
    model: str,
    operation: str,
    record_id: Optional[int] = None,
    domain: Optional[str] = None,
    fields: Optional[List[str]] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    order: Optional[str] = None,
    ids: Optional[List[int]] = None,
) -> str:
    """Build an Odoo URI from components.

    Args:
        model: Odoo model name (e.g., "res.partner")
        operation: Operation type (record, search, browse, count, fields)
        record_id: Record ID for record operation
        domain: Odoo domain expression (should be URL-encoded if needed)
        fields: List of field names to return
        limit: Maximum number of records
        offset: Pagination offset
        order: Sorting criteria
        ids: List of IDs for browse operation

    Returns:
        Formatted URI string

    Raises:
        URIValidationError: If the components are invalid
    """
    # Validate model name
    if not _is_valid_model_name(model):
        raise URIValidationError(f"Invalid model name: {model}")

    # Validate operation
    if operation not in [op.value for op in OdooOperation]:
        raise URIValidationError(f"Invalid operation: {operation}")

    # Build base URI
    if operation == "record":
        if not record_id:
            raise URIValidationError("Record operation requires an ID")
        uri = f"odoo://{model}/record/{record_id}"
    else:
        uri = f"odoo://{model}/{operation}"

    # Add query parameters
    params = {}
    if domain is not None:
        params["domain"] = domain
    if fields:
        params["fields"] = ",".join(fields)
    if limit is not None:
        params["limit"] = str(limit)
    if offset is not None:
        params["offset"] = str(offset)
    if order is not None:
        params["order"] = order
    if ids:
        params["ids"] = ",".join(str(id_val) for id_val in ids)

    if params:
        query_string = urllib.parse.urlencode(params)
        uri = f"{uri}?{query_string}"

    return uri


def build_search_uri(
    model: str,
    domain: Optional[str] = None,
    fields: Optional[List[str]] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    order: Optional[str] = None,
) -> str:
    """Build a search URI for the given model and parameters.

    This is a convenience function for building search URIs.
    """
    return build_uri(
        model, "search", domain=domain, fields=fields, limit=limit, offset=offset, order=order
    )


def build_record_uri(model: str, record_id: int) -> str:
    """Build a record URI for a specific record.

    This is a convenience function for building record URIs.
    """
    return build_uri(model, "record", record_id=record_id)


def build_pagination_uri(base_uri: str, offset: int, limit: int) -> str:
    """Build a pagination URI from a base URI.

    Args:
        base_uri: The base URI to paginate
        offset: The new offset value
        limit: The limit value

    Returns:
        URI with updated offset and limit parameters
    """
    parsed = parse_uri(base_uri)
    parsed.offset = offset
    parsed.limit = limit
    return parsed.to_uri()


def extract_model_from_uri(uri: str) -> str:
    """Extract just the model name from a URI.

    Args:
        uri: The URI to extract from

    Returns:
        The model name

    Raises:
        URIParseError: If the URI is invalid
    """
    parsed = parse_uri(uri)
    return parsed.model


def _is_valid_model_name(model: str) -> bool:
    """Check if a model name is valid.

    Odoo model names must:
    - Start with a letter
    - Contain only letters, numbers, dots, and underscores
    - Not be empty
    """
    if not model:
        return False
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9_.]*$", model))


def _parse_query_parameters(query_string: str) -> Dict[str, str]:
    """Parse URL query parameters."""
    # parse_qsl returns a list of tuples, filter out empty values
    params = {}
    for key, value in urllib.parse.parse_qsl(query_string, keep_blank_values=True):
        params[key] = value
    return params


def _parse_fields_parameter(fields_str: Optional[str]) -> Optional[List[str]]:
    """Parse comma-separated fields parameter."""
    if not fields_str:
        return None
    return [f.strip() for f in fields_str.split(",") if f.strip()]


def _parse_int_parameter(value: Optional[str], param_name: str) -> Optional[int]:
    """Parse an integer parameter."""
    if value is None:
        return None
    try:
        result = int(value)
        if result < 0:
            raise URIValidationError(f"{param_name} must be non-negative, got: {result}")
        return result
    except ValueError:
        raise URIValidationError(f"Invalid {param_name} value: {value}") from None


def _parse_ids_parameter(ids_str: Optional[str]) -> Optional[List[int]]:
    """Parse comma-separated IDs parameter."""
    if not ids_str:
        return None
    try:
        return [int(id_str.strip()) for id_str in ids_str.split(",") if id_str.strip()]
    except ValueError:
        raise URIValidationError(f"Invalid IDs parameter: {ids_str}") from None
