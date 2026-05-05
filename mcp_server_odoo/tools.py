"""MCP tool handlers for Odoo operations.

This module implements MCP tools for performing operations on Odoo data.
Tools are different from resources - they can have side effects and perform
actions like creating, updating, or deleting records.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .access_control import AccessControlError, AccessController
from .config import OdooConfig
from .connection_protocol import OdooConnectionProtocol
from .error_handling import (
    NotFoundError,
    ValidationError,
)
from .error_sanitizer import ErrorSanitizer
from .logging_config import get_logger, perf_logger
from .odoo_connection import OdooConnectionError
from .schemas import (
    BinaryFieldResult,
    BulkCreateResult,
    BulkDeleteResult,
    BulkUpdateResult,
    CreateResult,
    DeleteResult,
    FieldSelectionMetadata,
    ImportResult,
    ModelsResult,
    PostMessageResult,
    RecordResult,
    ResourceTemplatesResult,
    SearchResult,
    ServerInfoResult,
    UpdateResult,
)

if TYPE_CHECKING:
    from .registry import ConnectionRegistry
    from .usage import UsageTracker

logger = get_logger(__name__)

MAX_BULK_SIZE = 1000  # Maximum records per bulk operation
MAX_BINARY_SIZE_BYTES = 25 * 1024 * 1024  # set_binary_field upload cap
MAX_CONCURRENT_BINARY_UPLOADS = 3  # parallel set_binary_field calls; higher OOMs the server
_BINARY_UPLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_BINARY_UPLOADS)
_AVATAR_FIELD_RE = re.compile(r"^avatar_(128|256|512|1024|1920)$")

# ContextVar to pass the current user's sub from _get_user_context to the wrapper
_current_sub: contextvars.ContextVar[str] = contextvars.ContextVar("_current_sub", default="stdio")


class OdooToolHandler:
    """Handles MCP tool requests for Odoo operations."""

    def __init__(
        self,
        app: FastMCP,
        connection: Optional[OdooConnectionProtocol] = None,
        access_controller: Optional[AccessController] = None,
        config: Optional[OdooConfig] = None,
        registry: Optional[ConnectionRegistry] = None,
        usage_tracker: Optional[UsageTracker] = None,
    ):
        """Initialize tool handler.

        Two modes:
        - Multi-tenant (HTTP): pass registry, connection resolved per-request via auth context
        - Single-tenant (stdio): pass connection + access_controller directly
        """
        self.app = app
        self.registry = registry
        self.connection = connection
        self.access_controller = access_controller
        self.config = config
        self.usage_tracker = usage_tracker

        # Register tools
        self._register_tools()

    async def _get_user_context(
        self,
    ) -> Tuple[OdooConnectionProtocol, AccessController, str]:
        """Get connection and access controller for the current request.

        In HTTP mode with OAuth, reads the authenticated user's subject ID
        from the auth context and resolves the connection via the registry.
        In stdio mode, returns the fallback connection.

        Returns:
            Tuple of (connection, access_controller, zitadel_sub)

        Raises:
            ValidationError: If no connection is available
            RateLimitExceeded: If user has exceeded their daily limit
        """
        if self.registry is not None:
            from mcp.server.auth.middleware.auth_context import get_access_token

            access_token = get_access_token()
            if access_token is None:
                raise ValidationError("No authentication token available")
            sub = access_token.client_id
            # Set early so callers that catch downstream errors (e.g. server_info)
            # can still emit usage/diagnostic events tied to the right user.
            _current_sub.set(sub)

            # Check rate limit before doing any work
            if self.usage_tracker:
                await self.usage_tracker.check_rate_limit(sub)

            cached = await self.registry.get_connection(sub)
            return cached.connection, cached.access_controller, sub

        # Stdio mode: use direct connection (no rate limiting)
        if self.connection is not None and self.access_controller is not None:
            _current_sub.set("stdio")
            return self.connection, self.access_controller, "stdio"

        raise ValidationError("No Odoo connection available")

    def _track_usage(self, sub: str, tool_name: str) -> None:
        """Fire-and-forget usage tracking. No-op if tracker not configured."""
        if self.usage_tracker and sub != "stdio":
            self.usage_tracker.record_usage_fire_and_forget(sub, tool_name)

    def _format_datetime(self, value: str) -> str:
        """Format datetime values to ISO 8601 with timezone."""
        if not value or not isinstance(value, str):
            return value

        # Handle Odoo's compact datetime format (YYYYMMDDTHH:MM:SS)
        if len(value) == 17 and "T" in value and "-" not in value:
            try:
                dt = datetime.strptime(value, "%Y%m%dT%H:%M:%S")
                return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            except ValueError:
                pass

        # Handle standard Odoo datetime format (YYYY-MM-DD HH:MM:SS)
        if " " in value and len(value) == 19:
            try:
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            except ValueError:
                pass

        return value

    def _process_record_dates(
        self,
        record: Dict[str, Any],
        model: str,
        connection: Optional[OdooConnectionProtocol] = None,
    ) -> Dict[str, Any]:
        """Process datetime fields in a record to ensure proper formatting."""
        conn = connection or self.connection
        # Common datetime field names in Odoo
        known_datetime_fields = {
            "create_date",
            "write_date",
            "date",
            "datetime",
            "date_start",
            "date_end",
            "date_from",
            "date_to",
            "date_order",
            "date_invoice",
            "date_due",
            "last_update",
            "last_activity",
            "activity_date_deadline",
        }

        # First try to get field metadata
        fields_info = None
        try:
            fields_info = conn.fields_get(model)
        except Exception:
            # Field metadata unavailable, will use fallback detection
            pass

        # Process each field in the record
        for field_name, field_value in record.items():
            if not isinstance(field_value, str):
                continue

            should_format = False

            # Check if field is identified as datetime from metadata
            if fields_info and isinstance(fields_info, dict) and field_name in fields_info:
                field_type = fields_info[field_name].get("type")
                if field_type == "datetime":
                    should_format = True

            # Check if field name suggests it's a datetime field
            if not should_format and field_name in known_datetime_fields:
                should_format = True

            # Check if field name ends with common datetime suffixes
            if not should_format and any(
                field_name.endswith(suffix) for suffix in ["_date", "_datetime", "_time"]
            ):
                should_format = True

            # Pattern-based detection for datetime-like strings
            if not should_format and (
                (
                    len(field_value) == 17 and "T" in field_value and "-" not in field_value
                )  # 20250607T21:55:52
                or (
                    len(field_value) == 19 and " " in field_value and field_value.count("-") == 2
                )  # 2025-06-07 21:55:52
            ):
                should_format = True

            # Apply formatting if needed
            if should_format:
                formatted = self._format_datetime(field_value)
                if formatted != field_value:
                    record[field_name] = formatted

        return record

    def _should_include_field_by_default(self, field_name: str, field_info: Dict[str, Any]) -> bool:
        """Determine if a field should be included in default response.

        Args:
            field_name: Name of the field
            field_info: Field metadata from fields_get()

        Returns:
            True if field should be included in default response
        """
        # Always include essential fields
        always_include = {"id", "name", "display_name", "active", "company_id"}
        if field_name in always_include:
            return True

        # Exclude system/technical fields by prefix
        exclude_prefixes = ("_", "message_", "activity_", "website_message_")
        if field_name.startswith(exclude_prefixes):
            return False

        # Exclude specific technical fields
        exclude_fields = {
            "write_date",
            "create_date",
            "write_uid",
            "create_uid",
            "__last_update",
            "access_token",
            "access_warning",
            "access_url",
        }
        if field_name in exclude_fields:
            return False

        # Get field type
        field_type = field_info.get("type", "")

        # Exclude binary and large fields
        if field_type in ("binary", "image", "html"):
            return False

        # Exclude expensive computed fields (non-stored)
        if field_info.get("compute") and not field_info.get("store", True):
            return False

        # Exclude one2many and many2many fields (can be large)
        if field_type in ("one2many", "many2many"):
            return False

        # Include required fields
        if field_info.get("required"):
            return True

        # Include simple stored fields that are searchable
        if field_info.get("store", True) and field_info.get("searchable", True):
            if field_type in (
                "char",
                "text",
                "boolean",
                "integer",
                "float",
                "date",
                "datetime",
                "selection",
                "many2one",
            ):
                return True

        return False

    def _score_field_importance(self, field_name: str, field_info: Dict[str, Any]) -> int:
        """Score field importance for smart default selection.

        Args:
            field_name: Name of the field
            field_info: Field metadata from fields_get()

        Returns:
            Importance score (higher = more important)
        """
        # Tier 1: Essential fields (always included)
        if field_name in {"id", "name", "display_name", "active"}:
            return 1000

        # Exclude system/technical fields by prefix
        exclude_prefixes = ("_", "message_", "activity_", "website_message_")
        if field_name.startswith(exclude_prefixes):
            return 0

        # Exclude specific technical fields
        exclude_fields = {
            "write_date",
            "create_date",
            "write_uid",
            "create_uid",
            "__last_update",
            "access_token",
            "access_warning",
            "access_url",
        }
        if field_name in exclude_fields:
            return 0

        score = 0

        # Tier 2: Required fields are very important
        if field_info.get("required"):
            score += 500

        # Tier 3: Field type importance
        field_type = field_info.get("type", "")
        type_scores = {
            "char": 200,
            "boolean": 180,
            "selection": 170,
            "integer": 160,
            "float": 160,
            "monetary": 140,
            "date": 150,
            "datetime": 150,
            "many2one": 120,  # Relations useful but not primary
            "text": 80,
            "one2many": 40,
            "many2many": 40,  # Heavy relations
            "binary": 10,
            "html": 10,
            "image": 10,  # Heavy content
        }
        score += type_scores.get(field_type, 50)

        # Tier 4: Storage and searchability bonuses
        if field_info.get("store", True):
            score += 80
        if field_info.get("searchable", True):
            score += 40

        # Tier 5: Business-relevant field patterns (bonus)
        business_patterns = [
            "state",
            "status",
            "stage",
            "priority",
            "company",
            "currency",
            "amount",
            "total",
            "date",
            "user",
            "partner",
            "email",
            "phone",
            "address",
            "street",
            "city",
            "country",
            "code",
            "ref",
            "number",
        ]
        if any(pattern in field_name.lower() for pattern in business_patterns):
            score += 60

        # Exclude expensive computed fields (non-stored)
        if field_info.get("compute") and not field_info.get("store", True):
            score = min(score, 30)  # Cap computed fields at low score

        # Exclude large field types completely
        if field_type in ("binary", "image", "html"):
            return 0

        # Exclude one2many and many2many fields (can be large)
        if field_type in ("one2many", "many2many"):
            return 0

        return max(score, 0)

    def _get_smart_default_fields(
        self, model: str, connection: Optional[OdooConnectionProtocol] = None
    ) -> Optional[List[str]]:
        """Get smart default fields for a model using field importance scoring.

        Args:
            model: The Odoo model name
            connection: Odoo connection to use (falls back to self.connection)

        Returns:
            List of field names to include by default, or None if unable to determine
        """
        conn = connection or self.connection
        try:
            # Get all field definitions
            fields_info = conn.fields_get(model)

            # Score all fields by importance
            field_scores = []
            for field_name, field_info in fields_info.items():
                score = self._score_field_importance(field_name, field_info)
                if score > 0:  # Only include fields with positive scores
                    field_scores.append((field_name, score))

            # Sort by score (highest first)
            field_scores.sort(key=lambda x: x[1], reverse=True)

            # Select top N fields based on configuration
            max_fields = self.config.max_smart_fields
            selected_fields = [field_name for field_name, _ in field_scores[:max_fields]]

            # Ensure essential fields are always included
            essential_fields = ["id", "name", "display_name", "active"]
            for field in essential_fields:
                if field in fields_info and field not in selected_fields:
                    selected_fields.append(field)

            # Remove duplicates while preserving order
            final_fields = []
            seen = set()
            for field in selected_fields:
                if field not in seen:
                    final_fields.append(field)
                    seen.add(field)

            # Ensure we have at least essential fields
            if not final_fields:
                final_fields = [f for f in essential_fields if f in fields_info]

            logger.debug(
                f"Smart default fields for {model}: {len(final_fields)} of {len(fields_info)} fields "
                f"(max configured: {max_fields})"
            )
            return final_fields

        except Exception as e:
            logger.warning(f"Could not determine default fields for {model}: {e}")
            # Return None to indicate we should get all fields
            return None

    def _register_tools(self):
        """Register all tool handlers with FastMCP."""

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

        @self.app.tool(
            title="List Models",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def list_models() -> ModelsResult:
            """List all models enabled for MCP access with their allowed operations.

            Returns:
                List of models with their technical names, display names,
                and allowed operations (read, write, create, unlink).
            """
            result = await self._handle_list_models_tool()
            self._track_usage(_current_sub.get(), "list_models")
            return ModelsResult(**result)

        @self.app.tool(
            title="List Resource Templates",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def list_resource_templates() -> ResourceTemplatesResult:
            """List available resource URI templates.

            Since MCP resources with parameters are registered as templates,
            they don't appear in the standard resource list. This tool provides
            information about available resource patterns you can use.

            Returns:
                Resource template definitions with examples and enabled models.
            """
            result = await self._handle_list_resource_templates_tool()
            self._track_usage(_current_sub.get(), "list_resource_templates")
            return ResourceTemplatesResult(**result)

        @self.app.tool(
            title="Server Info",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def server_info() -> ServerInfoResult:
            """Get MCP server version and connection status.

            Returns:
                Server version, git commit, API version, and Odoo connection status.
            """
            from .server import _BUILD_ORIGIN, GIT_COMMIT, SERVER_VERSION

            try:
                connection, _ac, _sub = await self._get_user_context()
                is_connected = (
                    connection.is_authenticated
                    if hasattr(connection, "is_authenticated")
                    else False
                )
                api_version = self.config.api_version if self.config else "json2"
                # Use the connection's actual URL (tenant URL), not the global config
                odoo_url = getattr(connection, "_base_url", None) or (
                    self.config.url if self.config else "multi-tenant"
                )
            except Exception:
                is_connected = False
                api_version = self.config.api_version if self.config else "unknown"
                odoo_url = "not connected"

            # Fetch companies for context (helps with multi-company setups)
            companies = []
            if is_connected:
                try:
                    companies = connection.search_read(
                        "res.company", [], fields=["id", "name"], limit=10
                    )
                except Exception:
                    pass

            self._track_usage(_current_sub.get(), "server_info")
            return ServerInfoResult(
                version=SERVER_VERSION,
                git_commit=GIT_COMMIT,
                api_version=api_version,
                odoo_url=odoo_url,
                connected=is_connected,
                runtime_id=_BUILD_ORIGIN,
                companies=companies,
            )

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
        ) -> BulkCreateResult:
            """Create multiple records in a single operation (max 1000).

            Much faster than calling create_record repeatedly. Use this when
            importing data, creating batches of records, or any scenario with
            more than a few records.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                vals_list: List of dicts, each containing field values for one record.
                    Example: [{"name": "Alice"}, {"name": "Bob"}]

            Returns:
                List of created record IDs with count and confirmation.
            """
            result = await self._handle_create_records_tool(model, vals_list)
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
        ) -> BulkUpdateResult:
            """Update multiple records with the same values in a single operation (max 1000).

            Use this for mass updates like tagging contacts, changing statuses,
            or applying the same change to many records at once.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_ids: List of record IDs to update
                values: Field values to apply to all specified records

            Returns:
                List of updated record IDs with count and confirmation.
            """
            result = await self._handle_update_records_tool(model, record_ids, values)
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
        ) -> BulkDeleteResult:
            """Delete multiple records in a single operation (max 1000).

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_ids: List of record IDs to delete

            Returns:
                List of deleted record IDs with count and confirmation.
            """
            result = await self._handle_delete_records_tool(model, record_ids)
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

            Returns:
                Import result with counts of created/updated records and any errors.
            """
            result = await self._handle_import_records_tool(model, fields, data, context)
            self._track_usage(_current_sub.get(), "import_records")
            return ImportResult(**result)

        # --- Binary Field Upload ---

        @self.app.tool(
            title="Set Binary Field (Upload Image/File to a Record Field)",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def set_binary_field(
            model: str,
            record_id: int,
            field_name: str,
            source: str,
        ) -> BinaryFieldResult:
            """Upload bytes into a Binary or Image field on an existing record.

            Use this for: avatar/logo on res.partner, product images on product.template,
            company logo, attachment bytes on custom Binary fields.

            IMPORTANT — bytes must NOT pass through the model. `source` must be an
            http(s) URL; the server fetches the bytes from that URL directly and
            streams them to Odoo. Do NOT base64-encode a local file and paste it
            into this call — LLMs are for reasoning, not binary transport.

            If the user has a local file without a URL, direct them to upload it
            somewhere reachable first: a Google Drive share link (direct download),
            Dropbox direct link, S3 pre-signed URL, Imgur, etc. They then give you
            the URL.

            For BULK import of files/documents into Odoo, use the Documents app
            (`documents.document`) — users can drop files into a Documents folder
            directly through the Odoo UI or a share-link, and you can then query
            the resulting records via search_records.

            For res.partner avatars: pass field_name='image_1920'. The avatar_*
            fields are computed from image_1920 and auto-resize. If you pass
            'avatar_1920' this tool auto-redirects to 'image_1920' and warns.

            For attaching PDFs/documents to a specific record (e.g. a bonnetje
            on a sale.order), use create_record on ir.attachment with
            {name, datas, res_model, res_id} instead.

            Args:
                model: Odoo model name (e.g. 'res.partner', 'product.template')
                record_id: ID of the record to update
                field_name: Binary or Image field name on that model
                source: http(s) URL the server will fetch. Max 25 MB.

            Returns:
                Written field name, size in bytes, and record URL.
            """
            result = await self._handle_set_binary_field_tool(model, record_id, field_name, source)
            self._track_usage(_current_sub.get(), "set_binary_field")
            return BinaryFieldResult(**result)

        # --- Chatter: post_message ---

        @self.app.tool(
            title="Post Chatter Message (Send Message / Log Note)",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,  # mt_comment sends real email; mt_note may also if partner_ids set
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def post_message(
            model: str,
            record_id: int,
            body: str,
            subject: Optional[str] = None,
            partner_ids: Optional[List[int]] = None,
            attachment_ids: Optional[List[int]] = None,
            subtype_xmlid: str = "mail.mt_comment",
            cc: Optional[str] = None,
        ) -> PostMessageResult:
            """Post a message in the chatter of any thread-enabled Odoo record.

            Equivalent to clicking 'Send Message' (subtype=mt_comment, default)
            or 'Log Note' (subtype=mt_note) in the Odoo UI. Sends synchronously
            within the same request — no waiting on the email queue cron.

            Args:
                model: Odoo model with chatter enabled — 'res.partner', 'crm.lead',
                    'sale.order', 'account.move', 'helpdesk.ticket', etc.
                record_id: ID of the record to post on.
                body: HTML body of the message. Plain strings are HTML-escaped by Odoo.
                subject: Optional subject line. Defaults to the record's display_name
                    when omitted on a non-note message.
                partner_ids: Explicit recipients (res.partner ids). Notifies them on
                    top of subscribed followers. NB: setting this on a note (mt_note)
                    still creates mail.notification + mail.mail for these partners.
                attachment_ids: ir.attachment ids to link to the message. Pre-create
                    via create_record on ir.attachment with {name, datas, res_model,
                    res_id} — this is required because inline byte transport over
                    XML-RPC fails.
                subtype_xmlid: 'mail.mt_comment' (default — sends email to followers)
                    or 'mail.mt_note' (silent internal note, hidden from portal users).
                cc: Comma-separated extra emails to notify (Odoo v19+ only).
                    On older Odoos this raises a clear error.

            Returns:
                Posted mail.message details including per-recipient delivery state
                (mail.notification rows) and, if pan_outlook_pro is installed and the
                send went via Microsoft Graph, the Outlook message-id.
            """
            result = await self._handle_post_message_tool(
                model=model,
                record_id=record_id,
                body=body,
                subject=subject,
                partner_ids=partner_ids,
                attachment_ids=attachment_ids,
                subtype_xmlid=subtype_xmlid,
                cc=cc,
            )
            self._track_usage(_current_sub.get(), "post_message")
            return PostMessageResult(**result)

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

    async def _handle_list_models_tool(self) -> Dict[str, Any]:
        """Handle list models tool request with permissions."""
        try:
            connection, access_controller, sub = await self._get_user_context()
            with perf_logger.track_operation("tool_list_models"):
                # Get models from MCP access controller
                models = access_controller.get_enabled_models()

                # In JSON/2 mode, get_enabled_models() returns [] because Odoo
                # handles ACLs server-side. Fetch models from ir.model instead.
                if not models and hasattr(connection, "search_read"):
                    try:
                        ir_models = connection.search_read(
                            "ir.model",
                            [["transient", "=", False]],
                            fields=["model", "name"],
                            order="model asc",
                        )
                        models = [{"model": m["model"], "name": m["name"]} for m in ir_models]
                    except Exception as e:
                        logger.warning(f"Could not fetch models from ir.model: {e}")
                        models = []

                # Return model list without per-model permission checks.
                # Permissions are enforced per-operation to avoid N×4 API calls.
                return {"models": models}
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error in list_models tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to list models: {sanitized_msg}") from e

    async def _handle_list_resource_templates_tool(self) -> Dict[str, Any]:
        """Handle list resource templates tool request."""
        try:
            _, access_controller, sub = await self._get_user_context()
            # Get list of enabled models that can be used with resources
            enabled_models = access_controller.get_enabled_models()
            model_names = [m["model"] for m in enabled_models if m.get("read", True)]

            # Define the resource templates
            templates = [
                {
                    "uri_template": "odoo://{model}/record/{record_id}",
                    "description": "Get a specific record by ID",
                    "parameters": {
                        "model": "Odoo model name (e.g., res.partner)",
                        "record_id": "Record ID (e.g., 10)",
                    },
                    "example": "odoo://res.partner/record/10",
                },
                {
                    "uri_template": "odoo://{model}/search",
                    "description": "Basic search returning first 10 records",
                    "parameters": {
                        "model": "Odoo model name",
                    },
                    "example": "odoo://res.partner/search",
                    "note": "Query parameters are not supported. Use search_records tool for advanced queries.",
                },
                {
                    "uri_template": "odoo://{model}/count",
                    "description": "Count all records in a model",
                    "parameters": {
                        "model": "Odoo model name",
                    },
                    "example": "odoo://res.partner/count",
                    "note": "Query parameters are not supported. Use search_records tool for filtered counts.",
                },
                {
                    "uri_template": "odoo://{model}/fields",
                    "description": "Get field definitions for a model",
                    "parameters": {"model": "Odoo model name"},
                    "example": "odoo://res.partner/fields",
                },
            ]

            # Return the resource template information
            return {
                "templates": templates,
                "enabled_models": model_names[:10],  # Show first 10 as examples
                "total_models": len(model_names),
                "note": "Resource URIs do not support query parameters. Use tools (search_records, get_record) for advanced operations with filtering, pagination, and field selection.",
            }

        except Exception as e:
            logger.error(f"Error in list_resource_templates tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to list resource templates: {sanitized_msg}") from e

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

    # --- Bulk Operation Handlers ---

    async def _handle_create_records_tool(
        self,
        model: str,
        vals_list: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Handle bulk create tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context()
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

                created_ids = connection.create_bulk(model, vals_list)

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
    ) -> Dict[str, Any]:
        """Handle bulk update tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context()
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

                connection.write(model, record_ids, values)

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
    ) -> Dict[str, Any]:
        """Handle bulk delete tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context()
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

                connection.unlink(model, record_ids)

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
    ) -> Dict[str, Any]:
        """Handle import_records tool request using Odoo's load() method."""
        try:
            connection, access_controller, sub = await self._get_user_context()
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

                result = connection.load_records(model, fields, str_data, safe_context)

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

    async def _handle_set_binary_field_tool(
        self,
        model: str,
        record_id: int,
        field_name: str,
        source: str,
    ) -> Dict[str, Any]:
        """Handle set_binary_field tool request.

        Fetches bytes from `source` (http(s) URL only — data: URIs are rejected
        so bytes never pass through the LLM), validates the target field is
        Binary/Image, auto-redirects avatar_* writes to image_1920, and writes
        the base64 string via connection.write.
        """
        try:
            async with _BINARY_UPLOAD_SEMAPHORE:
                connection, access_controller, sub = await self._get_user_context()
                with perf_logger.track_operation("tool_set_binary_field", model=model):
                    access_controller.validate_model_access(model, "write")
                    if not connection.is_authenticated:
                        raise ValidationError("Not authenticated with Odoo")
                    if not field_name:
                        raise ValidationError("field_name is required")
                    if not source:
                        raise ValidationError("source is required (http(s) URL)")

                    # --- Fetch bytes from URL ---
                    # Reject data: URIs: they would force the LLM to carry the full
                    # base64 payload in the tool call, which defeats the purpose of
                    # this tool. The user should upload the file somewhere reachable
                    # (Drive/Dropbox/S3/etc.) and pass the URL.
                    if source.startswith("data:"):
                        raise ValidationError(
                            "data: URIs are not accepted — bytes must not pass through the "
                            "LLM. Upload the file to a reachable URL (Google Drive share, "
                            "Dropbox direct link, S3 pre-signed URL, etc.) and pass the URL."
                        )
                    parsed = urlparse(source)
                    if parsed.scheme not in ("http", "https"):
                        raise ValidationError(
                            f"source must be an http(s) URL, got scheme '{parsed.scheme}'"
                        )
                    if not parsed.netloc:
                        raise ValidationError("source URL is missing a host")
                    try:
                        async with httpx.AsyncClient(
                            timeout=30.0,
                            follow_redirects=True,
                            max_redirects=5,
                        ) as client:
                            chunks: List[bytes] = []
                            total = 0
                            async with client.stream("GET", source) as resp:
                                resp.raise_for_status()
                                async for chunk in resp.aiter_bytes(chunk_size=65536):
                                    total += len(chunk)
                                    if total > MAX_BINARY_SIZE_BYTES:
                                        raise ValidationError(
                                            f"Source exceeds max size of "
                                            f"{MAX_BINARY_SIZE_BYTES // (1024 * 1024)} MB"
                                        )
                                    chunks.append(chunk)
                            raw_bytes = b"".join(chunks)
                    except ValidationError:
                        raise
                    except httpx.HTTPError as e:
                        raise ValidationError(f"Failed to fetch source URL: {e}") from e

                    if not raw_bytes:
                        raise ValidationError("Source produced zero bytes")

                    # --- Validate record exists ---
                    existing = connection.read(model, [record_id], ["id"])
                    if not existing:
                        raise NotFoundError(f"Record not found: {model} with ID {record_id}")

                    # --- Validate field type + auto-redirect avatar_* ---
                    fields_info = connection.fields_get(model)
                    if not isinstance(fields_info, dict):
                        raise ValidationError(f"Could not introspect fields of {model}")

                    target_field = field_name
                    warning: Optional[str] = None

                    # Avatar fields on avatar.mixin are compute-only without inverse;
                    # writing to them is a silent no-op. Redirect to image_1920 if present.
                    if _AVATAR_FIELD_RE.match(field_name) and "image_1920" in fields_info:
                        target_field = "image_1920"
                        warning = (
                            f"'{field_name}' is a computed field without an inverse; "
                            f"wrote to 'image_1920' instead (avatar/image variants recompute automatically)"
                        )

                    # product.product.image_1920 has a fall-through inverse: if the
                    # template image is empty OR the template has only one active
                    # variant, the write lands on product.template instead of the
                    # variant. Use 'image_variant_1920' to force variant-specific
                    # storage. Don't auto-redirect (user may legitimately want the
                    # template-wide write); just warn.
                    if (
                        model == "product.product"
                        and field_name == "image_1920"
                        and "image_variant_1920" in fields_info
                    ):
                        warning = (
                            "writes to product.product.image_1920 may fall through to "
                            "product.template (if template image is empty or only one "
                            "active variant exists). Use field_name='image_variant_1920' "
                            "for guaranteed variant-specific storage."
                        )

                    if target_field not in fields_info:
                        raise ValidationError(
                            f"Field '{target_field}' does not exist on model '{model}'"
                        )

                    ftype = fields_info[target_field].get("type")
                    if ftype not in ("binary", "image"):
                        raise ValidationError(
                            f"Field '{target_field}' is type '{ftype}', not binary/image"
                        )
                    if fields_info[target_field].get("readonly"):
                        raise ValidationError(f"Field '{target_field}' on '{model}' is readonly")

                    # --- Write (Odoo ORM creates/updates backing ir.attachment) ---
                    b64 = base64.b64encode(raw_bytes).decode("ascii")
                    success = connection.write(model, [record_id], {target_field: b64})

                    base_url = (
                        getattr(connection, "_base_url", None)
                        or (self.config.url if self.config else "")
                    ).rstrip("/")
                    record_url = f"{base_url}/web#id={record_id}&model={model}&view_type=form"

                    message = f"Wrote {len(raw_bytes)} bytes to {model}({record_id}).{target_field}"
                    if warning:
                        message = f"{message}. Note: {warning}"

                    return {
                        "success": bool(success),
                        "model": model,
                        "record_id": record_id,
                        "field": target_field,
                        "size_bytes": len(raw_bytes),
                        "url": record_url,
                        "message": message,
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
            logger.error(f"Error in set_binary_field tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to set binary field: {sanitized_msg}") from e

    async def _call_record_method(
        self,
        connection: OdooConnectionProtocol,
        model: str,
        record_ids: List[int],
        method: str,
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Invoke a method on a recordset via the transport-agnostic call_method.

        Generic helper for tools that wrap an Odoo recordset method
        (`record.foo(...)` rather than CRUD). Works on both XML-RPC
        and JSON/2 transports.

        Future tools (post_invoice, confirm_sale_order, etc.) reuse this.
        """
        return connection.call_method(model, method, ids=list(record_ids), **(kwargs or {}))

    async def _handle_post_message_tool(
        self,
        model: str,
        record_id: int,
        body: str,
        subject: Optional[str] = None,
        partner_ids: Optional[List[int]] = None,
        attachment_ids: Optional[List[int]] = None,
        subtype_xmlid: str = "mail.mt_comment",
        cc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle post_message tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context()
            with perf_logger.track_operation("tool_post_message", model=model):
                # Posting to chatter requires write access on the model
                access_controller.validate_model_access(model, "write")

                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                if not body or not body.strip():
                    raise ValidationError("body is required and cannot be empty")

                # Verify record exists
                existing = connection.read(model, [record_id], ["id"])
                if not existing:
                    raise NotFoundError(f"Record not found: {model} with ID {record_id}")

                # Build kwargs for message_post — only include fields the user set,
                # so we don't override Odoo's own defaults (e.g. subject from display_name).
                # body_is_html=True is essential over RPC: Odoo's message_post escapes
                # plain str bodies (it expects markupsafe.Markup for HTML), but Markup
                # objects can't traverse XML-RPC / JSON-RPC. Without this flag, "<p>x</p>"
                # arrives in the chatter as literal "&lt;p&gt;x&lt;/p&gt;".
                kwargs: Dict[str, Any] = {
                    "body": body,
                    "body_is_html": True,
                    "message_type": "comment",
                    "subtype_xmlid": subtype_xmlid,
                }
                if subject is not None:
                    kwargs["subject"] = subject
                if partner_ids:
                    kwargs["partner_ids"] = list(partner_ids)
                if attachment_ids:
                    kwargs["attachment_ids"] = list(attachment_ids)
                if cc:
                    # Odoo v19+ only — older Odoos raise:
                    # ValueError: Those values are not supported when posting or notifying: outgoing_email_to
                    kwargs["outgoing_email_to"] = cc

                raw = await self._call_record_method(
                    connection, model, [record_id], "message_post", kwargs
                )
                # message_post returns the new mail.message id; some transports
                # wrap singletons in a list — normalize.
                if isinstance(raw, list):
                    if not raw:
                        raise ValidationError("message_post returned empty result")
                    message_id = raw[0]
                else:
                    message_id = raw
                if not isinstance(message_id, int):
                    raise ValidationError(f"Unexpected message_post return: {raw!r}")

                # Read message back for subtype/attachment summary
                msg_fields = ["subtype_id", "attachment_ids"]
                # x_microsoft_message_id only exists when pan_outlook_pro is installed
                outlook_field = "x_microsoft_message_id"
                try:
                    available = connection.fields_get(
                        "mail.message", [outlook_field], allfields=False
                    )
                except TypeError:
                    available = connection.fields_get("mail.message", [outlook_field])
                except Exception:
                    available = {}
                if outlook_field in (available or {}):
                    msg_fields.append(outlook_field)

                msg_rows = connection.read("mail.message", [message_id], msg_fields)
                msg = msg_rows[0] if msg_rows else {}
                subtype_pair = msg.get("subtype_id")
                subtype_name = (
                    subtype_pair[1]
                    if isinstance(subtype_pair, list) and len(subtype_pair) > 1
                    else None
                )
                attachments = msg.get("attachment_ids") or []
                outlook_msg_id = msg.get(outlook_field) if outlook_field in msg_fields else None
                if outlook_msg_id is False:
                    outlook_msg_id = None

                # Read notifications fan-out
                notif_rows = connection.search_read(
                    "mail.notification",
                    [("mail_message_id", "=", message_id)],
                    [
                        "res_partner_id",
                        "notification_type",
                        "notification_status",
                        "failure_reason",
                    ],
                )
                notifications: List[Dict[str, Any]] = []
                for n in notif_rows:
                    p = n.get("res_partner_id") or [None, ""]
                    notifications.append(
                        {
                            "partner_id": p[0] if isinstance(p, list) else None,
                            "partner_name": p[1] if isinstance(p, list) and len(p) > 1 else "",
                            "type": n.get("notification_type") or "",
                            "status": n.get("notification_status") or "",
                            "failure_reason": n.get("failure_reason") or None,
                        }
                    )

                base_url = (
                    getattr(connection, "_base_url", None)
                    or (self.config.url if self.config else "")
                ).rstrip("/")
                record_url = f"{base_url}/web#id={record_id}&model={model}&view_type=form"

                send_count = sum(1 for n in notifications if n["status"] == "sent")
                fail_count = sum(1 for n in notifications if n["status"] == "exception")
                summary_bits = [f"posted mail.message {message_id}"]
                if notifications:
                    summary_bits.append(
                        f"{len(notifications)} notification(s): {send_count} sent, {fail_count} failed"
                    )
                if outlook_msg_id:
                    summary_bits.append("sent via Microsoft Graph")

                return {
                    "success": True,
                    "message_id": message_id,
                    "subtype": subtype_name,
                    "attachment_count": len(attachments),
                    "notifications": notifications,
                    "outlook_pro_message_id": outlook_msg_id,
                    "record_url": record_url,
                    "message": "; ".join(summary_bits),
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
            logger.error(f"Error in post_message tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to post message: {sanitized_msg}") from e


def register_tools(
    app: FastMCP,
    connection: Optional[OdooConnectionProtocol] = None,
    access_controller: Optional[AccessController] = None,
    config: Optional[OdooConfig] = None,
    registry: Optional[ConnectionRegistry] = None,
    usage_tracker: Optional[UsageTracker] = None,
) -> OdooToolHandler:
    """Register all Odoo tools with the FastMCP app.

    Args:
        app: FastMCP application instance
        connection: Odoo connection instance (stdio/single-tenant mode)
        access_controller: Access control instance (stdio/single-tenant mode)
        config: Odoo configuration instance
        registry: ConnectionRegistry for multi-tenant mode (HTTP)
        usage_tracker: UsageTracker for rate limiting and usage logging

    Returns:
        The tool handler instance
    """
    handler = OdooToolHandler(
        app,
        registry=registry,
        connection=connection,
        access_controller=access_controller,
        config=config,
        usage_tracker=usage_tracker,
    )
    logger.info("Registered Odoo MCP tools")
    return handler
