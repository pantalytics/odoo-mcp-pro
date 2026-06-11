"""Search and get-record MCP tools."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from mcp.types import ToolAnnotations

from ..access_control import AccessControlError
from ..error_handling import NotFoundError, ValidationError
from ..error_sanitizer import ErrorSanitizer
from ..logging_config import perf_logger
from ..odoo_connection import OdooConnectionError
from ..schemas import FieldSelectionMetadata, RecordResult, SearchResult
from ._common import _current_sub, logger


class QueryToolsMixin:
    """search_records and get_record tools."""

    def _register_query_tools(self):
        """Register query tool handlers with FastMCP."""

        @self.app.tool(
            title="Search Records",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def search_records(
            model: str,
            domain: Optional[Any] = None,
            fields: Optional[Any] = None,
            limit: int = 100,
            offset: int = 0,
            order: Optional[str] = None,
        ) -> SearchResult:
            """Search for records in an Odoo model.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                domain: Odoo domain filter - can be:
                    - A list: [['is_company', '=', True]]
                    - A JSON string: "[['is_company', '=', true]]"
                    - None: returns all records (default)
                fields: Field selection options - can be:
                    - None (default): Returns smart selection of common fields
                    - A list: ["field1", "field2", ...] - Returns only specified fields
                    - A JSON string: '["field1", "field2"]' - Parsed to list
                    - ["__all__"] or '["__all__"]': Returns ALL fields (warning: may cause serialization errors)
                limit: Maximum number of records to return
                offset: Number of records to skip
                order: Sort order (e.g., 'name asc')

            Returns:
                Search results with records, total count, and pagination info
            """
            result = await self._handle_search_tool(model, domain, fields, limit, offset, order)
            self._track_usage(_current_sub.get(), "search_records")
            return SearchResult(**result)

        @self.app.tool(
            title="Get Record",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def get_record(
            model: str,
            record_id: int,
            fields: Optional[List[str]] = None,
        ) -> RecordResult:
            """Get a specific record by ID with smart field selection.

            This tool supports selective field retrieval to optimize performance and response size.
            By default, returns a smart selection of commonly-used fields based on the model's field metadata.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_id: The record ID
                fields: Field selection options:
                    - None (default): Returns smart selection of common fields
                    - ["field1", "field2", ...]: Returns only specified fields
                    - ["__all__"]: Returns ALL fields (warning: can be very large)

            Workflow for field discovery:
            1. To see all available fields for a model, use the resource:
               read("odoo://res.partner/fields")
            2. Then request specific fields:
               get_record("res.partner", 1, fields=["name", "email", "phone"])

            Examples:
                # Get smart defaults (recommended)
                get_record("res.partner", 1)

                # Get specific fields only
                get_record("res.partner", 1, fields=["name", "email", "phone"])

                # Get ALL fields (use with caution)
                get_record("res.partner", 1, fields=["__all__"])

            Returns:
                Record data with requested fields. When using smart defaults,
                includes metadata with field statistics.
            """
            result = await self._handle_get_record_tool(model, record_id, fields)
            self._track_usage(_current_sub.get(), "get_record")
            return result

    async def _handle_search_tool(
        self,
        model: str,
        domain: Optional[Any],
        fields: Optional[Any],
        limit: int,
        offset: int,
        order: Optional[str],
    ) -> Dict[str, Any]:
        """Handle search tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context()
            with perf_logger.track_operation("tool_search", model=model):
                # Check model access
                access_controller.validate_model_access(model, "read")

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # Handle domain parameter - can be string or list
                parsed_domain = []
                if domain is not None:
                    if isinstance(domain, str):
                        # Parse string to list
                        try:
                            # First try standard JSON parsing
                            parsed_domain = json.loads(domain)
                        except json.JSONDecodeError:
                            # If that fails, try converting single quotes to double quotes
                            # This handles Python-style domain strings
                            try:
                                # Replace single quotes with double quotes for valid JSON
                                # But be careful not to replace quotes inside string values
                                json_domain = domain.replace("'", '"')
                                # Also need to ensure Python True/False are lowercase for JSON
                                json_domain = json_domain.replace("True", "true").replace(
                                    "False", "false"
                                )
                                parsed_domain = json.loads(json_domain)
                            except json.JSONDecodeError as e:
                                raise ValidationError(
                                    f"Invalid domain parameter. Expected JSON array, got: {domain[:100]}..."
                                ) from e

                        if not isinstance(parsed_domain, list):
                            raise ValidationError(
                                f"Domain must be a list, got {type(parsed_domain).__name__}"
                            )
                        logger.debug(f"Parsed domain from string: {parsed_domain}")
                    else:
                        # Already a list
                        parsed_domain = domain

                # Handle fields parameter - can be string or list
                parsed_fields = fields
                if fields is not None and isinstance(fields, str):
                    # Parse string to list
                    try:
                        parsed_fields = json.loads(fields)
                        if not isinstance(parsed_fields, list):
                            raise ValidationError(
                                f"Fields must be a list, got {type(parsed_fields).__name__}"
                            )
                    except json.JSONDecodeError as e:
                        raise ValidationError(
                            f"Invalid fields parameter. Expected JSON array, got: {fields[:100]}..."
                        ) from e

                # Set defaults
                if limit <= 0 or limit > self.config.max_limit:
                    limit = self.config.default_limit

                # Get total count
                total_count = connection.search_count(model, parsed_domain)

                # Search for records
                record_ids = connection.search(
                    model, parsed_domain, limit=limit, offset=offset, order=order
                )

                # Determine which fields to fetch
                fields_to_fetch = parsed_fields
                if parsed_fields is None:
                    # Use smart field selection to avoid serialization issues
                    fields_to_fetch = self._get_smart_default_fields(model, connection)
                    logger.debug(
                        f"Using smart defaults for {model} search: {len(fields_to_fetch) if fields_to_fetch else 'all'} fields"
                    )
                elif parsed_fields == ["__all__"]:
                    # Explicit request for all fields
                    fields_to_fetch = None  # Odoo interprets None as all fields
                    logger.debug(f"Fetching all fields for {model} search")

                # Read records
                records = []
                if record_ids:
                    records = connection.read(model, record_ids, fields_to_fetch)
                    # Process datetime fields in each record
                    records = [
                        self._process_record_dates(record, model, connection) for record in records
                    ]

                return {
                    "records": records,
                    "total": total_count,
                    "limit": limit,
                    "offset": offset,
                    "model": model,
                }

        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in search_records tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Search failed: {sanitized_msg}") from e

    async def _handle_get_record_tool(
        self,
        model: str,
        record_id: int,
        fields: Optional[List[str]],
    ) -> RecordResult:
        """Handle get record tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context()
            with perf_logger.track_operation("tool_get_record", model=model):
                # Check model access
                access_controller.validate_model_access(model, "read")

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # Determine which fields to fetch
                fields_to_fetch = fields
                use_smart_defaults = False
                total_fields = None
                field_selection_method = "explicit"

                if fields is None:
                    # Use smart field selection
                    fields_to_fetch = self._get_smart_default_fields(model, connection)
                    use_smart_defaults = True
                    field_selection_method = "smart_defaults"
                    logger.debug(
                        f"Using smart defaults for {model}: {len(fields_to_fetch) if fields_to_fetch else 'all'} fields"
                    )
                elif fields == ["__all__"]:
                    # Explicit request for all fields
                    fields_to_fetch = None  # Odoo interprets None as all fields
                    field_selection_method = "all"
                    logger.debug(f"Fetching all fields for {model}")
                else:
                    # Specific fields requested
                    logger.debug(f"Fetching specific fields for {model}: {fields}")

                # Read the record
                records = connection.read(model, [record_id], fields_to_fetch)

                if not records:
                    raise ValidationError(f"Record not found: {model} with ID {record_id}")

                # Process datetime fields in the record
                record = self._process_record_dates(records[0], model, connection)

                # Build metadata when using smart defaults
                metadata = None
                if use_smart_defaults:
                    try:
                        all_fields_info = connection.fields_get(model)
                        total_fields = len(all_fields_info)
                    except Exception:
                        pass

                    metadata = FieldSelectionMetadata(
                        fields_returned=len(record),
                        field_selection_method=field_selection_method,
                        total_fields_available=total_fields,
                        note=f"Limited fields returned for performance. Use fields=['__all__'] for all fields or see odoo://{model}/fields for available fields.",
                    )

                return RecordResult(record=record, metadata=metadata)

        except ValidationError:
            raise
        except NotFoundError as e:
            raise ValidationError(str(e)) from e
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in get_record tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to get record: {sanitized_msg}") from e
