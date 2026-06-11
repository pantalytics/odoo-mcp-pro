"""Test suite for MCP tools functionality.

Access-control and error-path tests for the tool handler live in
tests/test_tools_crud.py. Shared fixtures live in tests/helpers/tool_fixtures.py.
"""

from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from mcp_server_odoo.access_control import AccessController
from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_connection import OdooConnection
from mcp_server_odoo.tools import OdooToolHandler, register_tools
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


class TestOdooToolHandler:
    """Test cases for OdooToolHandler class."""

    def test_handler_initialization(self, handler, mock_app):
        """Test handler is properly initialized."""
        assert handler.app == mock_app
        assert handler.connection is not None
        assert handler.access_controller is not None
        assert handler.config is not None

    def test_tools_registered(self, handler, mock_app):
        """Test that tools are registered with FastMCP."""
        # Check that all three tools are registered
        assert "search_records" in mock_app._tools
        assert "get_record" in mock_app._tools
        assert "list_models" in mock_app._tools

    @pytest.mark.asyncio
    async def test_search_records_success(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test successful search_records operation."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.search_count.return_value = 5
        mock_connection.search.return_value = [1, 2, 3]
        mock_connection.read.return_value = [
            {"id": 1, "name": "Record 1"},
            {"id": 2, "name": "Record 2"},
            {"id": 3, "name": "Record 3"},
        ]

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Call the tool
        result = await search_records(
            model="res.partner",
            domain=[["is_company", "=", True]],
            fields=["name", "email"],
            limit=3,
            offset=0,
            order="name asc",
        )

        # Verify result (SearchResult is a Pydantic model)
        assert result.model == "res.partner"
        assert result.total == 5
        assert result.limit == 3
        assert result.offset == 0
        assert len(result.records) == 3

        # Verify calls
        mock_access_controller.validate_model_access.assert_called_once_with("res.partner", "read")
        mock_connection.search_count.assert_called_once_with(
            "res.partner", [["is_company", "=", True]]
        )
        mock_connection.search.assert_called_once_with(
            "res.partner", [["is_company", "=", True]], limit=3, offset=0, order="name asc"
        )

    @pytest.mark.asyncio
    async def test_search_records_with_domain_operators(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test search_records with Odoo domain operators like |, &, !."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.search_count.return_value = 10
        mock_connection.search.return_value = [1, 2, 3]
        mock_connection.read.return_value = [
            {"id": 1, "name": "Partner 1", "state_id": [13, "California"]},
            {"id": 2, "name": "Partner 2", "state_id": [13, "California"]},
            {"id": 3, "name": "Partner 3", "state_id": [14, "CA"]},
        ]

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Test with OR operator
        domain_with_or = [
            ["country_id", "=", 233],
            "|",
            ["state_id.name", "ilike", "California"],
            ["state_id.code", "=", "CA"],
        ]

        result = await search_records(
            model="res.partner", domain=domain_with_or, fields=["name", "state_id"], limit=10
        )

        # Verify result (SearchResult is a Pydantic model)
        assert result.model == "res.partner"
        assert result.total == 10
        assert len(result.records) == 3

        # Verify the domain was passed correctly
        mock_connection.search_count.assert_called_with("res.partner", domain_with_or)
        mock_connection.search.assert_called_with(
            "res.partner", domain_with_or, limit=10, offset=0, order=None
        )

    @pytest.mark.asyncio
    async def test_search_records_with_string_domain(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test search_records with domain as JSON string (Claude Desktop format)."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.search_count.return_value = 1
        mock_connection.search.return_value = [15]
        mock_connection.read.return_value = [
            {"id": 15, "name": "Azure Interior", "is_company": True},
        ]

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Domain as JSON string (as sent by Claude Desktop)
        domain_string = '[["is_company", "=", true], ["name", "ilike", "azure interior"]]'

        result = await search_records(model="res.partner", domain=domain_string, limit=5)

        # Verify result (SearchResult is a Pydantic model)
        assert result.model == "res.partner"
        assert result.total == 1
        assert len(result.records) == 1
        assert result.records[0]["name"] == "Azure Interior"

        # Verify the domain was parsed and passed correctly as a list
        expected_domain = [["is_company", "=", True], ["name", "ilike", "azure interior"]]
        mock_connection.search_count.assert_called_with("res.partner", expected_domain)
        mock_connection.search.assert_called_with(
            "res.partner", expected_domain, limit=5, offset=0, order=None
        )

    @pytest.mark.asyncio
    async def test_search_records_with_python_style_domain(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test search_records with Python-style domain string (single quotes)."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.search_count.return_value = 1
        mock_connection.search.return_value = [15]
        mock_connection.read.return_value = [
            {"id": 15, "name": "Azure Interior", "is_company": True},
        ]

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Domain with single quotes (Python style)
        domain_string = "[['name', 'ilike', 'azure interior'], ['is_company', '=', True]]"

        result = await search_records(model="res.partner", domain=domain_string, limit=5)

        # Verify result (SearchResult is a Pydantic model)
        assert result.model == "res.partner"
        assert result.total == 1
        assert len(result.records) == 1
        assert result.records[0]["name"] == "Azure Interior"

        # Verify the domain was parsed correctly
        expected_domain = [["name", "ilike", "azure interior"], ["is_company", "=", True]]
        mock_connection.search_count.assert_called_with("res.partner", expected_domain)

    @pytest.mark.asyncio
    async def test_search_records_with_string_fields(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test search_records with fields as JSON string."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.search_count.return_value = 1
        mock_connection.search.return_value = [15]
        mock_connection.read.return_value = [
            {"id": 15, "name": "Azure Interior", "is_company": True},
        ]

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Fields as JSON string (as sometimes sent by Claude Desktop)
        fields_string = '["name", "is_company", "id"]'

        result = await search_records(
            model="res.partner", domain=[["is_company", "=", True]], fields=fields_string, limit=5
        )

        # Verify result (SearchResult is a Pydantic model)
        assert result.model == "res.partner"
        assert result.total == 1

        # Verify fields were parsed correctly
        mock_connection.read.assert_called_with("res.partner", [15], ["name", "is_company", "id"])

    @pytest.mark.asyncio
    async def test_search_records_with_complex_domain(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test search_records with complex nested domain operators."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.search_count.return_value = 5
        mock_connection.search.return_value = [1, 2]
        mock_connection.read.return_value = [
            {"id": 1, "name": "Company A", "is_company": True},
            {"id": 2, "name": "Company B", "is_company": True},
        ]

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Complex domain with nested operators
        complex_domain = [
            "&",
            ["is_company", "=", True],
            "|",
            ["name", "ilike", "Company"],
            ["email", "!=", False],
        ]

        await search_records(model="res.partner", domain=complex_domain, limit=5)

        # Verify the domain was passed correctly
        mock_connection.search_count.assert_called_with("res.partner", complex_domain)
        mock_connection.search.assert_called_with(
            "res.partner", complex_domain, limit=5, offset=0, order=None
        )

    @pytest.mark.asyncio
    async def test_get_record_success(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test successful get_record operation."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.read.return_value = [
            {"id": 123, "name": "Test Partner", "email": "test@example.com"}
        ]

        # Get the registered get_record function
        get_record = mock_app._tools["get_record"]

        # Call the tool
        result = await get_record(model="res.partner", record_id=123, fields=["name", "email"])

        # Verify result — get_record returns RecordResult
        assert result.record["id"] == 123
        assert result.record["name"] == "Test Partner"
        assert result.record["email"] == "test@example.com"

        # Verify calls
        mock_access_controller.validate_model_access.assert_called_once_with("res.partner", "read")
        mock_connection.read.assert_called_once_with("res.partner", [123], ["name", "email"])

    @pytest.mark.asyncio
    async def test_list_models_success(
        self, handler, mock_connection, mock_access_controller, mock_app
    ):
        """Test successful list_models operation with permissions."""
        # Setup mocks for get_enabled_models
        mock_access_controller.get_enabled_models.return_value = [
            {"model": "res.partner", "name": "Contact"},
            {"model": "sale.order", "name": "Sales Order"},
        ]

        # Setup mocks for get_model_permissions
        from mcp_server_odoo.access_control import ModelPermissions

        partner_perms = ModelPermissions(
            model="res.partner",
            enabled=True,
            can_read=True,
            can_write=True,
            can_create=True,
            can_unlink=False,
        )

        order_perms = ModelPermissions(
            model="sale.order",
            enabled=True,
            can_read=True,
            can_write=False,
            can_create=False,
            can_unlink=False,
        )

        # Configure side_effect to return different permissions based on model
        def get_perms(model):
            if model == "res.partner":
                return partner_perms
            elif model == "sale.order":
                return order_perms
            else:
                raise Exception(f"Unknown model: {model}")

        mock_access_controller.get_model_permissions.side_effect = get_perms

        # Get the registered list_models function
        list_models = mock_app._tools["list_models"]

        # Call the tool
        result = await list_models()

        # Verify result structure (ModelsResult is a Pydantic model)
        assert len(result.models) == 2

        # Verify first model (res.partner)
        partner = result.models[0]
        assert partner.model == "res.partner"
        assert partner.name == "Contact"

        # Verify second model (sale.order)
        order = result.models[1]
        assert order.model == "sale.order"
        assert order.name == "Sales Order"

        # Verify calls
        mock_access_controller.get_enabled_models.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_records_with_defaults(
        self, handler, mock_connection, mock_access_controller, mock_app, valid_config
    ):
        """Test search_records with default values."""
        # Setup mocks
        mock_connection.search_count.return_value = 0
        mock_connection.search.return_value = []
        mock_connection.read.return_value = []

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Call with minimal params
        result = await search_records(model="res.partner")

        # Verify defaults were applied (SearchResult is a Pydantic model)
        assert result.limit == valid_config.default_limit
        assert result.offset == 0
        assert result.total == 0
        assert result.records == []

        # Verify domain default
        mock_connection.search_count.assert_called_with("res.partner", [])

    @pytest.mark.asyncio
    async def test_search_records_limit_validation(
        self, handler, mock_connection, mock_access_controller, mock_app, valid_config
    ):
        """Test search_records limit validation."""
        # Setup mocks
        mock_connection.search_count.return_value = 100
        mock_connection.search.return_value = []
        mock_connection.read.return_value = []

        # Get the registered search_records function
        search_records = mock_app._tools["search_records"]

        # Test with limit exceeding max
        result = await search_records(model="res.partner", limit=9999)

        # Should use default limit since 9999 > max_limit (SearchResult is a Pydantic model)
        assert result.limit == valid_config.default_limit

        # Test with negative limit
        result = await search_records(model="res.partner", limit=-1)

        # Should use default limit
        assert result.limit == valid_config.default_limit


class TestRegisterTools:
    """Test cases for register_tools function."""

    def test_register_tools_success(self):
        """Test successful registration of tools."""
        # Create mocks
        mock_app = MagicMock(spec=FastMCP)
        mock_connection = MagicMock(spec=OdooConnection)
        mock_access_controller = MagicMock(spec=AccessController)
        config = OdooConfig(
            url="http://localhost:8069",
            api_key="test_key",
            database="test_db",
        )

        # Register tools
        handler = register_tools(mock_app, mock_connection, mock_access_controller, config)

        # Verify handler is returned
        assert isinstance(handler, OdooToolHandler)
        assert handler.app == mock_app
        assert handler.connection == mock_connection
        assert handler.access_controller == mock_access_controller
        assert handler.config == config
