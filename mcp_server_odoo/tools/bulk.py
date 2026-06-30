"""Bulk create/update/delete and import MCP tools."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.types import ToolAnnotations

from ..access_control import AccessControlError
from ..error_handling import ValidationError
from ..error_sanitizer import ErrorSanitizer
from ..logging_config import perf_logger
from ..odoo_connection import OdooConnectionError
from ..schemas import (
    BulkCreateResult,
    BulkDeleteResult,
    BulkUpdateResult,
    ImportResult,
)
from ._common import MAX_BULK_SIZE, _current_sub, logger, run_blocking


class BulkToolsMixin:
    """create_records, update_records, delete_records and import_records tools."""

    def _register_bulk_tools(self):
        """Register bulk operation tool handlers with FastMCP."""

        # --- Bulk Operations ---

        @self.app.tool(
            title="Create Records (Bulk)",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def create_records(
            model: str,
            vals_list: List[Dict[str, Any]],
            connection: Optional[str] = None,
        ) -> BulkCreateResult:
            """Create multiple records in a single operation (max 1000).

            Much faster than calling create_record repeatedly. Use this when
            importing data, creating batches of records, or any scenario with
            more than a few records.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                vals_list: List of dicts, each containing field values for one record.
                    Example: [{"name": "Alice"}, {"name": "Bob"}]
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                List of created record IDs with count and confirmation.
            """
            result = await self._handle_create_records_tool(model, vals_list, connection)
            self._track_usage(_current_sub.get(), "create_records")
            return BulkCreateResult(**result)

        @self.app.tool(
            title="Update Records (Bulk)",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def update_records(
            model: str,
            record_ids: List[int],
            values: Dict[str, Any],
            connection: Optional[str] = None,
        ) -> BulkUpdateResult:
            """Update multiple records with the same values in a single operation (max 1000).

            Use this for mass updates like tagging contacts, changing statuses,
            or applying the same change to many records at once.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_ids: List of record IDs to update
                values: Field values to apply to all specified records
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                List of updated record IDs with count and confirmation.
            """
            result = await self._handle_update_records_tool(model, record_ids, values, connection)
            self._track_usage(_current_sub.get(), "update_records")
            return BulkUpdateResult(**result)

        @self.app.tool(
            title="Delete Records (Bulk)",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=False,
            ),
        )
        async def delete_records(
            model: str,
            record_ids: List[int],
            connection: Optional[str] = None,
        ) -> BulkDeleteResult:
            """Delete multiple records in a single operation (max 1000).

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_ids: List of record IDs to delete
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                List of deleted record IDs with count and confirmation.
            """
            result = await self._handle_delete_records_tool(model, record_ids, connection)
            self._track_usage(_current_sub.get(), "delete_records")
            return BulkDeleteResult(**result)

        # --- Import (load) ---

        @self.app.tool(
            title="Import Records (Upsert with External IDs)",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def import_records(
            model: str,
            fields: List[str],
            data: List[List[str]],
            context: Optional[Dict[str, Any]] = None,
            connection: Optional[str] = None,
        ) -> ImportResult:
            """Import records using Odoo's native load() method with external ID support.

            This is the recommended way to import data. It supports idempotent
            upsert: if a record with the given external ID already exists, it will
            be updated instead of duplicated. Running the same import twice
            produces the same result.

            Uses the same mechanism as Odoo's built-in CSV import.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                fields: List of field names matching the data columns.
                    Use 'id' for the external ID column (e.g., '__import__.partner_acme').
                    Use 'field_name/id' to reference related records by external ID
                    (e.g., 'parent_id/id' with value '__import__.partner_parent').
                    Use 'field_name/.id' to reference by database ID.
                data: List of rows, where each row is a list of string values.
                    All values must be strings. Example:
                    [["__import__.partner_acme", "Acme Corp", "True"]]
                context: Optional context dict. Useful flags:
                    - tracking_disable: True — suppress mail/activity notifications
                    - defer_fields_computation: True — batch computed field updates
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                Import result with counts of created/updated records and any errors.
            """
            result = await self._handle_import_records_tool(
                model, fields, data, context, connection
            )
            self._track_usage(_current_sub.get(), "import_records")
            return ImportResult(**result)

    # --- Bulk Operation Handlers ---

    async def _handle_create_records_tool(
        self,
        model: str,
        vals_list: List[Dict[str, Any]],
        connection_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle bulk create tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context(
                connection_selector, writes=True
            )
            with perf_logger.track_operation("tool_create_records", model=model):
                access_controller.validate_model_access(model, "create")
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")
                if not vals_list:
                    raise ValidationError("vals_list cannot be empty")
                if len(vals_list) > MAX_BULK_SIZE:
                    raise ValidationError(
                        f"Bulk create limited to {MAX_BULK_SIZE} records, got {len(vals_list)}"
                    )

                created_ids = await run_blocking(
                    connection, connection.create_bulk, model, vals_list
                )

                return {
                    "success": True,
                    "created_ids": created_ids,
                    "count": len(created_ids),
                    "model": model,
                    "message": f"Successfully created {len(created_ids)} {model} record(s)",
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in create_records tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Bulk create failed: {sanitized_msg}") from e

    async def _handle_update_records_tool(
        self,
        model: str,
        record_ids: List[int],
        values: Dict[str, Any],
        connection_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle bulk update tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context(
                connection_selector, writes=True
            )
            with perf_logger.track_operation("tool_update_records", model=model):
                access_controller.validate_model_access(model, "write")
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")
                if not record_ids:
                    raise ValidationError("record_ids cannot be empty")
                if not values:
                    raise ValidationError("values cannot be empty")
                if len(record_ids) > MAX_BULK_SIZE:
                    raise ValidationError(
                        f"Bulk update limited to {MAX_BULK_SIZE} records, got {len(record_ids)}"
                    )

                await run_blocking(connection, connection.write, model, record_ids, values)

                return {
                    "success": True,
                    "updated_ids": record_ids,
                    "count": len(record_ids),
                    "model": model,
                    "message": f"Successfully updated {len(record_ids)} {model} record(s)",
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in update_records tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Bulk update failed: {sanitized_msg}") from e

    async def _handle_delete_records_tool(
        self,
        model: str,
        record_ids: List[int],
        connection_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle bulk delete tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context(
                connection_selector, writes=True
            )
            with perf_logger.track_operation("tool_delete_records", model=model):
                access_controller.validate_model_access(model, "unlink")
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")
                if not record_ids:
                    raise ValidationError("record_ids cannot be empty")
                if len(record_ids) > MAX_BULK_SIZE:
                    raise ValidationError(
                        f"Bulk delete limited to {MAX_BULK_SIZE} records, got {len(record_ids)}"
                    )

                await run_blocking(connection, connection.unlink, model, record_ids)

                return {
                    "success": True,
                    "deleted_ids": record_ids,
                    "count": len(record_ids),
                    "model": model,
                    "message": f"Successfully deleted {len(record_ids)} {model} record(s)",
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in delete_records tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Bulk delete failed: {sanitized_msg}") from e

    async def _handle_import_records_tool(
        self,
        model: str,
        fields: List[str],
        data: List[List[str]],
        context: Optional[Dict[str, Any]] = None,
        connection_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle import_records tool request using Odoo's load() method."""
        try:
            connection, access_controller, sub = await self._get_user_context(
                connection_selector, writes=True
            )
            with perf_logger.track_operation("tool_import_records", model=model):
                access_controller.validate_model_access(model, "create")
                access_controller.validate_model_access(model, "write")
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")
                if not fields:
                    raise ValidationError("fields cannot be empty")
                if not data:
                    raise ValidationError("data cannot be empty")
                if len(data) > MAX_BULK_SIZE:
                    raise ValidationError(
                        f"Import limited to {MAX_BULK_SIZE} rows, got {len(data)}"
                    )

                # Validate that all rows have the same number of columns as fields
                for i, row in enumerate(data):
                    if len(row) != len(fields):
                        raise ValidationError(
                            f"Row {i} has {len(row)} values but {len(fields)} fields were specified"
                        )

                # Validate context keys (prevent dangerous keys like 'su')
                safe_context = None
                if context:
                    allowed_keys = {
                        "tracking_disable",
                        "defer_fields_computation",
                        "lang",
                        "tz",
                        "no_reset_password",
                    }
                    unsafe_keys = set(context.keys()) - allowed_keys
                    if unsafe_keys:
                        raise ValidationError(f"Context contains disallowed keys: {unsafe_keys}")
                    safe_context = context

                # Ensure all values are strings (Odoo load() expects strings)
                str_data = [[str(v) if v is not None else "" for v in row] for row in data]

                result = await run_blocking(
                    connection, connection.load_records, model, fields, str_data, safe_context
                )

                # Parse Odoo load() result
                ids = result.get("ids", []) or []
                messages = result.get("messages", []) or []

                # Filter out False/None from ids (failed rows return False)
                valid_ids = [id_ for id_ in ids if id_]

                # Separate errors from warnings
                errors = [
                    {
                        "row": msg.get("rows", {}).get("from", -1),
                        "message": msg.get("message", ""),
                        "type": msg.get("type", "error"),
                    }
                    for msg in messages
                    if msg.get("type") == "error"
                ]

                # Determine created vs updated counts
                # load() doesn't distinguish, but we can check if 'id' column was provided
                has_external_ids = "id" in fields
                success = len(errors) == 0

                # Build summary
                if success:
                    action = "imported (created/updated)" if has_external_ids else "created"
                    message = f"Successfully {action} {len(valid_ids)} {model} record(s)"
                else:
                    message = (
                        f"Import completed with {len(errors)} error(s). "
                        f"{len(valid_ids)} record(s) succeeded."
                    )

                return {
                    "success": success,
                    "imported": len(valid_ids),
                    "errors": errors,
                    "ids": valid_ids,
                    "model": model,
                    "message": message,
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in import_records tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Import failed: {sanitized_msg}") from e
