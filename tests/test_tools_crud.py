"""Access-control and error-path tests for MCP tools.

Split out of tests/test_tools.py to keep file sizes manageable.
Shared fixtures live in tests/helpers/tool_fixtures.py.
"""

import pytest

from mcp_server_odoo.access_control import AccessControlError
from mcp_server_odoo.error_handling import (
    ValidationError,
)
from mcp_server_odoo.odoo_connection import OdooConnectionError
from tests.helpers.tool_fixtures import (
    handler,
    mock_access_controller,
    mock_app,
    mock_connection,
    valid_config,
)

# Re-export shared fixtures so pytest can resolve them in this module
__all__ = [
    "handler",
    "mock_access_controller",
    "mock_app",
    "mock_connection",
    "valid_config",
]


class TestOdooToolHandlerErrors:
    """Access-control and error handling test cases for OdooToolHandler."""

    @pytest.mark.asyncio
    async def test_search_records_access_denied(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test search_records with access denied."""
        # Setup mocks
        mock_access_controller.validate_model_access.side_effect = AccessControlError(
            "Access denied"
        )

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Call the tool and expect error
        with pytest.raises(ValidationError) as exc_info:
            await search_records(model="res.partner", domain=[], fields=None, limit=10)

        assert "Access denied" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_search_records_not_authenticated(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test search_records when not authenticated."""
        # Setup mocks
        mock_connection.is_authenticated = False

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Call the tool and expect error
        with pytest.raises(ValidationError) as exc_info:
            await search_records(model="res.partner")

        assert "Not authenticated" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_search_records_connection_error(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test search_records with connection error."""
        # Setup mocks
        mock_connection.search_count.side_effect = OdooConnectionError("Connection lost")

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Call the tool and expect error
        with pytest.raises(ValidationError) as exc_info:
            await search_records(model="res.partner")

        assert "Connection error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_search_records_with_invalid_json_domain(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test search_records with invalid JSON string domain."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Invalid JSON string
        invalid_domain = '[["is_company", "=", true'  # Missing closing brackets

        # Should raise ValidationError
        with pytest.raises(ValidationError) as exc_info:
            await search_records(model="res.partner", domain=invalid_domain, limit=5)

        assert "Invalid search criteria format" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_record_not_found(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test get_record when record doesn't exist."""
        # Setup mocks
        mock_connection.read.return_value = []

        # Get the registered get_record function
        get_record = mock_app._tools["get_record"]

        # Call the tool and expect error
        with pytest.raises(ValidationError) as exc_info:
            await get_record(model="res.partner", record_id=999)

        assert "Record not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_record_access_denied(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test get_record with access denied."""
        # Setup mocks
        mock_access_controller.validate_model_access.side_effect = AccessControlError(
            "Access denied"
        )

        # Get the registered get_record function
        get_record = mock_app._tools["get_record"]

        # Call the tool and expect error
        with pytest.raises(ValidationError) as exc_info:
            await get_record(model="res.partner", record_id=1)

        assert "Access denied" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_record_not_authenticated(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test get_record when not authenticated."""
        # Setup mocks
        mock_connection.is_authenticated = False

        # Get the registered get_record function
        get_record = mock_app._tools["get_record"]

        # Call the tool and expect error
        with pytest.raises(ValidationError) as exc_info:
            await get_record(model="res.partner", record_id=1)

        assert "Not authenticated" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_record_connection_error(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test get_record with connection error."""
        # Setup mocks
        mock_connection.read.side_effect = OdooConnectionError("Connection lost")

        # Get the registered get_record function
        get_record = mock_app._tools["get_record"]

        # Call the tool and expect error
        with pytest.raises(ValidationError) as exc_info:
            await get_record(model="res.partner", record_id=1)

        assert "Connection error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_list_models_with_permission_failures(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test list_models when some models fail to get permissions."""
        # Setup mocks for get_enabled_models
        mock_access_controller.get_enabled_models.return_value = [
            {"model": "res.partner", "name": "Contact"},
            {"model": "unknown.model", "name": "Unknown Model"},
        ]

        # Setup mocks for get_model_permissions
        from mcp_server_odoo.access_control import AccessControlError, ModelPermissions

        partner_perms = ModelPermissions(
            model="res.partner",
            enabled=True,
            can_read=True,
            can_write=True,
            can_create=False,
            can_unlink=False,
        )

        # Configure side_effect to fail for unknown model
        def get_perms(model):
            if model == "res.partner":
                return partner_perms
            else:
                raise AccessControlError(f"Model {model} not found")

        mock_access_controller.get_model_permissions.side_effect = get_perms

        # Get the registered list_models function
        list_models = mock_app._tools["list_models"]

        # Call the tool - should not fail even if some models can't get permissions
        result = await list_models()

        # Verify result structure (ModelsResult is a Pydantic model)
        assert len(result.models) == 2

        # Verify models are returned without per-model permission checks
        partner = result.models[0]
        assert partner.model == "res.partner"

        unknown = result.models[1]
        assert unknown.model == "unknown.model"

    @pytest.mark.asyncio
    async def test_list_models_error(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test list_models with error."""
        # Setup mocks
        mock_access_controller.get_enabled_models.side_effect = Exception("API error")

        # Get the registered list_models function
        list_models = mock_app._tools["list_models"]

        # Call the tool and expect error
        with pytest.raises(ValidationError) as exc_info:
            await list_models()

        assert "Failed to list models" in str(exc_info.value)
