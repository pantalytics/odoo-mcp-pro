"""Pydantic models for structured tool output.

These models define the response schemas for MCP tools, enabling
automatic JSON schema generation and output validation by MCP clients.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# --- Search Records ---


class SearchResult(BaseModel):
    """Result of a record search operation."""

    records: List[Dict[str, Any]] = Field(description="List of matching records")
    total: int = Field(description="Total number of records matching the domain")
    limit: int = Field(description="Maximum records returned per page")
    offset: int = Field(description="Number of records skipped")
    model: str = Field(description="Odoo model name that was searched")


# --- Get Record ---


class FieldSelectionMetadata(BaseModel):
    """Metadata about which fields were returned and why."""

    fields_returned: int = Field(description="Number of fields in the response")
    field_selection_method: str = Field(
        description="How fields were selected (smart_defaults, explicit, all)"
    )
    total_fields_available: Optional[int] = Field(
        default=None, description="Total fields on the model"
    )
    note: Optional[str] = Field(
        default=None,
        description="Guidance on how to request more fields",
    )


class RecordResult(BaseModel):
    """Result of retrieving a single record by ID."""

    record: Dict[str, Any] = Field(description="Record data with requested fields")
    metadata: Optional[FieldSelectionMetadata] = Field(
        default=None,
        description="Field selection metadata (present when using smart defaults)",
    )


# --- List Models ---


class ModelOperations(BaseModel):
    """Allowed CRUD operations for a model."""

    read: bool = Field(description="Can read records")
    write: bool = Field(description="Can update records")
    create: bool = Field(description="Can create records")
    unlink: bool = Field(description="Can delete records")


class ModelInfo(BaseModel):
    """Information about an MCP-enabled Odoo model."""

    model: str = Field(description="Technical model name (e.g. 'res.partner')")
    name: str = Field(description="Human-readable model name")
    operations: Optional[ModelOperations] = Field(
        default=None, description="Allowed operations (standard mode only)"
    )


class ModelsResult(BaseModel):
    """Result of listing available models."""

    models: List[ModelInfo] = Field(description="List of available models")
    total: Optional[int] = Field(default=None, description="Total number of models")
    error: Optional[str] = Field(default=None, description="Error message if model listing failed")


# --- List Resource Templates ---


class ResourceTemplateParameter(BaseModel):
    """Parameter definition for a resource template."""

    model: str = Field(description="Odoo model name (e.g., res.partner)")
    record_id: Optional[str] = Field(default=None, description="Record ID (e.g., 10)")


class ResourceTemplateInfo(BaseModel):
    """Information about an available resource URI template."""

    uri_template: str = Field(description="URI template pattern")
    description: str = Field(description="What this resource provides")
    parameters: Dict[str, str] = Field(description="Template parameter descriptions")
    example: str = Field(description="Example URI")
    note: Optional[str] = Field(default=None, description="Additional usage notes")


class ResourceTemplatesResult(BaseModel):
    """Result of listing resource templates."""

    templates: List[ResourceTemplateInfo] = Field(description="Available resource templates")
    enabled_models: List[str] = Field(description="Sample of models usable with these templates")
    total_models: int = Field(description="Total number of enabled models")
    note: str = Field(description="Usage guidance for resources vs tools")


# --- Create Record ---


class CreateResult(BaseModel):
    """Result of creating a new record."""

    success: bool = Field(description="Whether the record was created successfully")
    record: Dict[str, Any] = Field(description="Essential fields of the created record")
    url: str = Field(description="Direct URL to the record in Odoo web interface")
    message: str = Field(description="Human-readable success message")


# --- Update Record ---


class UpdateResult(BaseModel):
    """Result of updating an existing record."""

    success: bool = Field(description="Whether the record was updated successfully")
    record: Dict[str, Any] = Field(description="Essential fields of the updated record")
    url: str = Field(description="Direct URL to the record in Odoo web interface")
    message: str = Field(description="Human-readable success message")


# --- Delete Record ---


class DeleteResult(BaseModel):
    """Result of deleting a record."""

    success: bool = Field(description="Whether the record was deleted successfully")
    deleted_id: int = Field(description="ID of the deleted record")
    deleted_name: str = Field(description="Display name of the deleted record")
    message: str = Field(description="Human-readable success message")


# --- Post Message (chatter) ---


class PostMessageNotification(BaseModel):
    """One mail.notification row created by a chatter post."""

    partner_id: int = Field(description="res.partner id of the recipient")
    partner_name: str = Field(description="Display name of the recipient")
    type: str = Field(description="notification_type — 'inbox' or 'email'")
    status: str = Field(description="notification_status — 'sent', 'exception', 'ready', etc.")
    failure_reason: Optional[str] = Field(
        default=None, description="Failure detail when status is 'exception'"
    )


class PostMessageResult(BaseModel):
    """Result of posting a chatter message via mail.thread.message_post."""

    success: bool = Field(description="Whether the post succeeded")
    message_id: int = Field(description="ID of the created mail.message")
    subtype: Optional[str] = Field(
        default=None,
        description="Subtype name — e.g. 'Discussions' (mt_comment) or 'Note' (mt_note)",
    )
    attachment_count: int = Field(
        default=0, description="Number of attachments linked to the message"
    )
    notifications: List[PostMessageNotification] = Field(
        default_factory=list,
        description="Per-recipient delivery rows. Empty for silent notes without explicit partner_ids.",
    )
    outlook_pro_message_id: Optional[str] = Field(
        default=None,
        description="x_microsoft_message_id when pan_outlook_pro is installed and the send went via Graph",
    )
    record_url: str = Field(description="Direct URL to the record in Odoo")
    degraded_details: List[str] = Field(
        default_factory=list,
        description=(
            "Detail sections that could not be read back after a successful post "
            "(e.g. 'message details', 'notification status'). The message itself "
            "was posted; only the follow-up enrichment failed."
        ),
    )
    message: str = Field(description="Human-readable summary")


# --- Bulk Operations ---


class BulkCreateResult(BaseModel):
    """Result of bulk creating records."""

    success: bool = Field(description="Whether all records were created successfully")
    created_ids: List[int] = Field(description="IDs of the created records")
    count: int = Field(description="Number of records created")
    model: str = Field(description="Odoo model name")
    message: str = Field(description="Human-readable success message")


class BulkUpdateResult(BaseModel):
    """Result of bulk updating records."""

    success: bool = Field(description="Whether all records were updated successfully")
    updated_ids: List[int] = Field(description="IDs of the updated records")
    count: int = Field(description="Number of records updated")
    model: str = Field(description="Odoo model name")
    message: str = Field(description="Human-readable success message")


class BulkDeleteResult(BaseModel):
    """Result of bulk deleting records."""

    success: bool = Field(description="Whether all records were deleted successfully")
    deleted_ids: List[int] = Field(description="IDs of the deleted records")
    count: int = Field(description="Number of records deleted")
    model: str = Field(description="Odoo model name")
    message: str = Field(description="Human-readable success message")


# --- Import (load) ---


class ImportResult(BaseModel):
    """Result of importing records via Odoo's load() method with external ID support."""

    success: bool = Field(description="Whether all records were imported successfully")
    imported: int = Field(description="Number of records imported (created or updated)")
    errors: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of errors with row index and message",
    )
    ids: List[int] = Field(
        default_factory=list,
        description="IDs of created/updated records",
    )
    model: str = Field(description="Odoo model name")
    message: str = Field(description="Human-readable summary message")


# --- Binary Field Upload ---


class BinaryFieldResult(BaseModel):
    """Result of writing a binary/image field on a record."""

    success: bool = Field(description="Whether the field was written successfully")
    model: str = Field(description="Odoo model name")
    record_id: int = Field(description="Record ID that was updated")
    field: str = Field(
        description="Binary field actually written (may differ from the requested field)"
    )
    size_bytes: int = Field(description="Size of the uploaded bytes (before base64 encoding)")
    url: str = Field(description="Direct URL to the record in Odoo web interface")
    message: str = Field(description="Human-readable success or warning message")


# --- Server Info ---


class ServerInfoResult(BaseModel):
    """Server version and connection status."""

    version: str = Field(description="MCP server version")
    git_commit: str = Field(description="Git commit hash of the running build")
    api_version: str = Field(description="Odoo API version (json2 or xmlrpc)")
    odoo_url: str = Field(description="Connected Odoo instance URL")
    database: Optional[str] = Field(
        default=None,
        description="Active Odoo database name. None on JSON/2 single-tenant where the API key resolves it server-side.",
    )
    connected: bool = Field(description="Whether the server is connected to Odoo")
    runtime_id: str = Field(description="Server runtime identifier")
    companies: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Available companies in the Odoo instance (id and name). Use company_id in search domains to filter by company.",
    )
