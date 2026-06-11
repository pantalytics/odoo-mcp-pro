"""Retrieval handlers for Odoo MCP resources."""

from __future__ import annotations

from typing import Optional

from ..access_control import AccessControlError
from ..error_handling import (
    ErrorContext,
    NotFoundError,
    PermissionError,
    ValidationError,
)
from ..logging_config import get_logger, perf_logger
from ..odoo_connection import OdooConnectionError

logger = get_logger(__name__)


class RetrievalMixin:
    """Resource request handlers for record retrieval, search, count, and fields."""

    async def _handle_record_retrieval(self, model: str, record_id: str) -> str:
        """Handle record retrieval request.

        Args:
            model: The Odoo model name
            record_id: The record ID to retrieve

        Returns:
            Formatted record data

        Raises:
            NotFoundError: If record doesn't exist
            PermissionError: If access is denied
            ValidationError: For invalid inputs
        """
        context = ErrorContext(model=model, operation="get_record", record_id=record_id)

        logger.info(f"Retrieving record: {model}/{record_id}")

        try:
            connection, access_controller = await self._get_user_context()
            with perf_logger.track_operation("resource_get_record", model=model):
                # Validate record ID
                try:
                    record_id_int = int(record_id)
                    if record_id_int <= 0:
                        raise ValueError("Record ID must be positive")
                except ValueError as e:
                    raise ValidationError(
                        f"Invalid record ID '{record_id}': {e}", context=context
                    ) from e

                # Check model access permissions
                try:
                    access_controller.validate_model_access(model, "read")
                except AccessControlError as e:
                    logger.warning(f"Access denied for {model}.read: {e}")
                    raise PermissionError(f"Access denied: {e}", context=context) from e

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo", context=context)

            # Search for the record to check if it exists
            record_ids = connection.search(model, [("id", "=", record_id_int)])

            if not record_ids:
                raise NotFoundError(
                    f"Record not found: {model} with ID {record_id} does not exist", context=context
                )

            # Read the record with smart field selection to avoid serialization issues
            # Get field metadata to determine which fields to fetch
            try:
                fields_info = connection.fields_get(model)
                # Filter out fields that might cause serialization issues
                safe_fields = []
                for field_name, field_info in fields_info.items():
                    field_type = field_info.get("type", "")
                    # Skip fields that commonly cause XML-RPC serialization issues
                    # Expanded list to include html fields which often contain Markup objects
                    problematic_types = ["binary", "serialized", "html"]
                    if (
                        field_type not in problematic_types
                        and not field_name.startswith("__")
                        and not field_name.startswith("_")
                    ):  # Also skip private fields
                        safe_fields.append(field_name)

                if safe_fields:
                    records = connection.read(model, record_ids, safe_fields)
                else:
                    # Fallback to all fields if we can't determine safe fields
                    records = connection.read(model, record_ids)
            except Exception as e:
                logger.debug(f"Could not get field metadata, reading all fields: {e}")
                # If we can't get field info, try to read all fields
                records = connection.read(model, record_ids)

            if not records:
                raise NotFoundError(f"Record not found: {model} with ID {record_id} does not exist")

            record = records[0]

            # Format the record data
            formatted_data = self._format_record(model, record, connection)

            logger.info(f"Successfully retrieved record: {model}/{record_id}")
            return formatted_data

        except (NotFoundError, PermissionError, ValidationError):
            # Re-raise our custom exceptions
            raise
        except OdooConnectionError as e:
            logger.error(f"Connection error retrieving {model}/{record_id}: {e}")
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error retrieving {model}/{record_id}: {e}")
            raise ValidationError(f"Failed to retrieve record: {e}") from e

    async def _handle_search(
        self,
        model: str,
        domain: Optional[str],
        fields: Optional[str],
        limit: Optional[int],
        offset: Optional[int],
        order: Optional[str],
    ) -> str:
        """Handle search request with domain filtering.

        Args:
            model: The Odoo model name
            domain: URL-encoded domain filter
            fields: Comma-separated list of fields
            limit: Maximum records to return
            offset: Pagination offset
            order: Sort order

        Returns:
            Formatted search results with pagination

        Raises:
            PermissionError: If access is denied
            ValidationError: For other errors
        """
        logger.info(f"Searching {model} with domain={domain}, limit={limit}, offset={offset}")

        try:
            connection, access_controller = await self._get_user_context()
            # Check model access permissions
            try:
                access_controller.validate_model_access(model, "read")
            except AccessControlError as e:
                logger.warning(f"Access denied for {model}.read: {e}")
                raise PermissionError(f"Access denied: {e}") from e

            # Ensure we're connected
            if not connection.is_authenticated:
                raise ValidationError("Not authenticated with Odoo")

            # Parse parameters
            parsed_domain = self._parse_domain(domain)
            fields_list = self._parse_fields(fields)
            limit_value = self._parse_limit(limit)
            offset_value = self._parse_offset(offset)
            order_value = self._parse_order(order)

            # Get total count for pagination
            total_count = connection.search_count(model, parsed_domain)

            # Perform search
            record_ids = connection.search(
                model, parsed_domain, limit=limit_value, offset=offset_value, order=order_value
            )

            # Read records if any found
            records = []
            if record_ids:
                records = connection.read(model, record_ids, fields_list)

            # Get field metadata for formatting
            try:
                fields_metadata = connection.fields_get(model)
            except Exception as e:
                logger.debug(f"Could not retrieve field metadata: {e}")
                fields_metadata = None

            # Format search results
            formatted_results = self._format_search_results(
                model,
                records,
                parsed_domain,
                fields_list,
                limit_value,
                offset_value,
                total_count,
                fields_metadata,
            )

            logger.info(f"Search completed: found {len(records)} of {total_count} records")
            return formatted_results

        except (PermissionError, ValidationError):
            # Re-raise our custom exceptions
            raise
        except OdooConnectionError as e:
            logger.error(f"Connection error searching {model}: {e}")
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error searching {model}: {e}")
            raise ValidationError(f"Failed to search records: {e}") from e

    async def _handle_count(self, model: str, domain: Optional[str]) -> str:
        """Handle count request with domain filtering.

        Args:
            model: The Odoo model name
            domain: URL-encoded domain filter

        Returns:
            Formatted count result

        Raises:
            PermissionError: If access is denied
            ValidationError: For other errors
        """
        logger.info(f"Counting {model} records with domain: {domain}")

        try:
            connection, access_controller = await self._get_user_context()
            # Check model access permissions
            try:
                access_controller.validate_model_access(model, "read")
            except AccessControlError as e:
                logger.warning(f"Access denied for {model}.read: {e}")
                raise PermissionError(f"Access denied: {e}") from e

            # Ensure we're connected
            if not connection.is_authenticated:
                raise ValidationError("Not authenticated with Odoo")

            # Parse domain
            parsed_domain = self._parse_domain(domain)

            # Get count
            count = connection.search_count(model, parsed_domain)

            # Format result
            formatted_result = self._format_count_result(model, count, parsed_domain)

            logger.info(f"Count completed: {count} records match criteria")
            return formatted_result

        except (PermissionError, ValidationError):
            # Re-raise our custom exceptions
            raise
        except OdooConnectionError as e:
            logger.error(f"Connection error counting {model}: {e}")
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error counting {model}: {e}")
            raise ValidationError(f"Failed to count records: {e}") from e

    async def _handle_fields(self, model: str) -> str:
        """Handle fields request for model introspection.

        Args:
            model: The Odoo model name

        Returns:
            Formatted field definitions

        Raises:
            PermissionError: If access is denied
            ValidationError: For other errors
        """
        logger.info(f"Getting field definitions for {model}")

        try:
            connection, access_controller = await self._get_user_context()
            # Check model access permissions
            try:
                access_controller.validate_model_access(model, "read")
            except AccessControlError as e:
                logger.warning(f"Access denied for {model}.read: {e}")
                raise PermissionError(f"Access denied: {e}") from e

            # Ensure we're connected
            if not connection.is_authenticated:
                raise ValidationError("Not authenticated with Odoo")

            # Get field definitions
            fields = connection.fields_get(model)

            # Format result
            formatted_result = self._format_fields_result(model, fields)

            logger.info(f"Fields retrieved: {len(fields)} fields found")
            return formatted_result

        except (PermissionError, ValidationError):
            # Re-raise our custom exceptions
            raise
        except OdooConnectionError as e:
            logger.error(f"Connection error getting fields for {model}: {e}")
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error getting fields for {model}: {e}")
            raise ValidationError(f"Failed to get field definitions: {e}") from e
