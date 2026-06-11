"""Single-record create/update/delete MCP tools."""

from __future__ import annotations

from typing import Any, Dict

from mcp.types import ToolAnnotations

from ..access_control import AccessControlError
from ..error_handling import NotFoundError, ValidationError
from ..error_sanitizer import ErrorSanitizer
from ..logging_config import perf_logger
from ..odoo_connection import OdooConnectionError
from ..schemas import CreateResult, DeleteResult, UpdateResult
from ._common import _current_sub, logger


class CrudToolsMixin:
    """create_record, update_record and delete_record tools."""

    def _register_crud_tools(self):
        """Register CRUD tool handlers with FastMCP."""

        @self.app.tool(
            title="Create Record",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def create_record(
            model: str,
            values: Dict[str, Any],
        ) -> CreateResult:
            """Create a new record in an Odoo model.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                values: Field values for the new record

            Returns:
                Created record details with ID, URL, and confirmation.
            """
            result = await self._handle_create_record_tool(model, values)
            self._track_usage(_current_sub.get(), "create_record")
            return CreateResult(**result)

        @self.app.tool(
            title="Update Record",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def update_record(
            model: str,
            record_id: int,
            values: Dict[str, Any],
        ) -> UpdateResult:
            """Update an existing record.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_id: The record ID to update
                values: Field values to update

            Returns:
                Updated record details with confirmation.
            """
            result = await self._handle_update_record_tool(model, record_id, values)
            self._track_usage(_current_sub.get(), "update_record")
            return UpdateResult(**result)

        @self.app.tool(
            title="Delete Record",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=False,
            ),
        )
        async def delete_record(
            model: str,
            record_id: int,
        ) -> DeleteResult:
            """Delete a record.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_id: The record ID to delete

            Returns:
                Deletion confirmation with the deleted record's name and ID.
            """
            result = await self._handle_delete_record_tool(model, record_id)
            self._track_usage(_current_sub.get(), "delete_record")
            return DeleteResult(**result)

    async def _handle_create_record_tool(
        self,
        model: str,
        values: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle create record tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context()
            with perf_logger.track_operation("tool_create_record", model=model):
                # Check model access
                access_controller.validate_model_access(model, "create")

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # Validate required fields
                if not values:
                    raise ValidationError("No values provided for record creation")

                # Create the record
                record_id = connection.create(model, values)

                # Return only essential fields to minimize context usage
                # Users can use get_record if they need more fields
                essential_fields = ["id", "name", "display_name"]

                # Filter to fields that actually exist on this model
                try:
                    model_fields = connection.fields_get(model, ["string", "type"])
                    essential_fields = [f for f in essential_fields if f in model_fields]
                    if "id" not in essential_fields:
                        essential_fields.insert(0, "id")
                except Exception:
                    essential_fields = ["id"]

                # Read only the essential fields
                records = connection.read(model, [record_id], essential_fields)
                if not records:
                    raise ValidationError(
                        f"Failed to read created record: {model} with ID {record_id}"
                    )

                # Process dates in the minimal record
                record = self._process_record_dates(records[0], model, connection)

                # Generate direct URL to the record in Odoo
                base_url = (
                    getattr(connection, "_base_url", None)
                    or (self.config.url if self.config else "")
                ).rstrip("/")
                record_url = f"{base_url}/web#id={record_id}&model={model}&view_type=form"

                return {
                    "success": True,
                    "record": record,
                    "url": record_url,
                    "message": f"Successfully created {model} record with ID {record_id}",
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in create_record tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to create record: {sanitized_msg}") from e

    async def _handle_update_record_tool(
        self,
        model: str,
        record_id: int,
        values: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle update record tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context()
            with perf_logger.track_operation("tool_update_record", model=model):
                # Check model access
                access_controller.validate_model_access(model, "write")

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # Validate input
                if not values:
                    raise ValidationError("No values provided for record update")

                # Check if record exists (only fetch ID to verify existence)
                existing = connection.read(model, [record_id], ["id"])
                if not existing:
                    raise NotFoundError(f"Record not found: {model} with ID {record_id}")

                # Update the record
                success = connection.write(model, [record_id], values)

                # Return only essential fields to minimize context usage
                # Users can use get_record if they need more fields
                essential_fields = ["id", "name", "display_name"]

                # Filter to fields that actually exist on this model
                try:
                    model_fields = connection.fields_get(model, ["string", "type"])
                    essential_fields = [f for f in essential_fields if f in model_fields]
                    if "id" not in essential_fields:
                        essential_fields.insert(0, "id")
                except Exception:
                    essential_fields = ["id"]

                # Read only the essential fields
                records = connection.read(model, [record_id], essential_fields)
                if not records:
                    raise ValidationError(
                        f"Failed to read updated record: {model} with ID {record_id}"
                    )

                # Process dates in the minimal record
                record = self._process_record_dates(records[0], model, connection)

                # Generate direct URL to the record in Odoo
                base_url = (
                    getattr(connection, "_base_url", None)
                    or (self.config.url if self.config else "")
                ).rstrip("/")
                record_url = f"{base_url}/web#id={record_id}&model={model}&view_type=form"

                return {
                    "success": success,
                    "record": record,
                    "url": record_url,
                    "message": f"Successfully updated {model} record with ID {record_id}",
                }

        except ValidationError:
            raise
        except NotFoundError as e:
            raise ValidationError(str(e)) from e
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in update_record tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to update record: {sanitized_msg}") from e

    async def _handle_delete_record_tool(
        self,
        model: str,
        record_id: int,
    ) -> Dict[str, Any]:
        """Handle delete record tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context()
            with perf_logger.track_operation("tool_delete_record", model=model):
                # Check model access
                access_controller.validate_model_access(model, "unlink")

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # Check if record exists
                existing = connection.read(model, [record_id])
                if not existing:
                    raise NotFoundError(f"Record not found: {model} with ID {record_id}")

                # Store some info about the record before deletion
                record_name = existing[0].get(
                    "name", existing[0].get("display_name", f"ID {record_id}")
                )

                # Delete the record
                success = connection.unlink(model, [record_id])

                return {
                    "success": success,
                    "deleted_id": record_id,
                    "deleted_name": record_name,
                    "message": f"Successfully deleted {model} record '{record_name}' (ID: {record_id})",
                }

        except ValidationError:
            raise
        except NotFoundError as e:
            raise ValidationError(str(e)) from e
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in delete_record tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to delete record: {sanitized_msg}") from e
