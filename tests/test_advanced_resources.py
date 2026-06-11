"""Tests for advanced resource operations (count, fields)."""

import json
from unittest.mock import Mock
from urllib.parse import quote

import pytest
from mcp.server.fastmcp import FastMCP

from mcp_server_odoo.access_control import AccessController
from mcp_server_odoo.config import OdooConfig, load_config
from mcp_server_odoo.odoo_connection import OdooConnection
from mcp_server_odoo.resources import OdooResourceHandler

# Import skip_on_rate_limit decorator
from .test_xmlrpc_operations import skip_on_rate_limit


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    config = Mock(spec=OdooConfig)
    config.default_limit = 10
    config.max_limit = 100
    return config


@pytest.fixture
def mock_connection():
    """Create a mock Odoo connection."""
    conn = Mock(spec=OdooConnection)
    conn.is_authenticated = True
    return conn


@pytest.fixture
def mock_access_controller():
    """Create a mock access controller."""
    controller = Mock(spec=AccessController)
    return controller


@pytest.fixture
def mock_app():
    """Create a mock FastMCP app."""
    app = Mock(spec=FastMCP)
    app.resource = Mock()

    # Store registered handlers
    app._handlers = {}

    def resource_decorator(uri_pattern, **kwargs):
        def decorator(func):
            app._handlers[uri_pattern] = func
            return func

        return decorator

    app.resource.side_effect = resource_decorator
    return app


@pytest.fixture
def resource_handler(mock_app, mock_connection, mock_access_controller, mock_config):
    """Create a resource handler instance."""
    return OdooResourceHandler(mock_app, mock_connection, mock_access_controller, mock_config)


class TestCountResource:
    """Test count resource functionality."""

    @pytest.mark.asyncio
    async def test_count_all_records(
        self, resource_handler, mock_connection, mock_access_controller
    ):
        """Test counting all records without domain."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.search_count.return_value = 150

        # Execute count
        result = await resource_handler._handle_count("res.partner", None)

        # Verify calls
        mock_access_controller.validate_model_access.assert_called_once_with("res.partner", "read")
        mock_connection.search_count.assert_called_once_with("res.partner", [])

        # Check result format
        assert "Count Result: res.partner" in result
        assert "Search criteria: All records" in result
        assert "Total count: 150 record(s)" in result

    @pytest.mark.asyncio
    async def test_count_with_domain(
        self, resource_handler, mock_connection, mock_access_controller
    ):
        """Test counting with domain filter."""
        # Setup domain
        domain = [["is_company", "=", True]]
        domain_encoded = quote(json.dumps(domain))

        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.search_count.return_value = 45

        # Execute count
        result = await resource_handler._handle_count("res.partner", domain_encoded)

        # Verify domain was parsed and used
        mock_connection.search_count.assert_called_once_with("res.partner", domain)

        # Check result contains domain info
        assert "Search criteria: is_company = True" in result
        assert "Total count: 45 record(s)" in result

    @pytest.mark.asyncio
    async def test_count_complex_domain(
        self, resource_handler, mock_connection, mock_access_controller
    ):
        """Test counting with complex domain."""
        # Setup complex domain
        domain = [
            "|",
            ["customer_rank", ">", 0],
            "&",
            ["is_company", "=", True],
            ["active", "=", True],
        ]
        domain_encoded = quote(json.dumps(domain))

        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.search_count.return_value = 25

        # Execute count
        result = await resource_handler._handle_count("res.partner", domain_encoded)

        # Check result contains complex domain
        assert "| customer_rank > 0 & is_company = True active = True" in result
        assert "Total count: 25 record(s)" in result

    @pytest.mark.asyncio
    async def test_count_zero_results(
        self, resource_handler, mock_connection, mock_access_controller
    ):
        """Test counting with no matching records."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.search_count.return_value = 0

        # Execute count
        result = await resource_handler._handle_count(
            "res.partner", quote(json.dumps([["name", "=", "NonExistent"]]))
        )

        # Check result
        assert "Total count: 0 record(s)" in result


class TestFieldsResource:
    """Test fields resource functionality."""

    @pytest.mark.asyncio
    async def test_fields_basic(self, resource_handler, mock_connection, mock_access_controller):
        """Test basic field retrieval."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.fields_get.return_value = {
            "name": {
                "type": "char",
                "string": "Name",
                "required": True,
                "help": "The partner name",
            },
            "email": {"type": "char", "string": "Email", "required": False},
            "is_company": {
                "type": "boolean",
                "string": "Is a Company",
                "help": "Check if the contact is a company, otherwise it is a person",
            },
            "partner_id": {
                "type": "many2one",
                "string": "Related Partner",
                "relation": "res.partner",
            },
        }

        # Execute fields
        result = await resource_handler._handle_fields("res.partner")

        # Verify calls
        mock_access_controller.validate_model_access.assert_called_once_with("res.partner", "read")
        mock_connection.fields_get.assert_called_once_with("res.partner")

        # Check result format
        assert "Field Definitions: res.partner" in result
        assert "Total fields: 4" in result
        assert "CHAR Fields (2):" in result
        assert "BOOLEAN Fields (1):" in result
        assert "MANY2ONE Fields (1):" in result

        # Check field details
        assert "name:" in result
        assert "Label: Name" in result
        assert "Required: True" in result
        assert "Help: The partner name" in result

        assert "partner_id:" in result
        assert "Related Model: res.partner" in result

    @pytest.mark.asyncio
    async def test_fields_with_selection(
        self, resource_handler, mock_connection, mock_access_controller
    ):
        """Test fields with selection type."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.fields_get.return_value = {
            "state": {
                "type": "selection",
                "string": "Status",
                "selection": [
                    ("draft", "Draft"),
                    ("open", "Open"),
                    ("done", "Done"),
                    ("cancel", "Cancelled"),
                ],
                "required": True,
            },
            "type": {
                "type": "selection",
                "string": "Type",
                "selection": [
                    ("contact", "Contact"),
                    ("invoice", "Invoice Address"),
                    ("delivery", "Delivery Address"),
                    ("other", "Other"),
                    ("private", "Private"),
                ],
            },
        }

        # Execute fields
        result = await resource_handler._handle_fields("res.partner")

        # Check selection fields
        assert "SELECTION Fields (2):" in result
        assert "state:" in result
        assert "Options: draft (Draft), open (Open), done (Done), cancel (Cancelled)" in result
        assert "type:" in result
        assert (
            "Options: contact (Contact), invoice (Invoice Address), delivery (Delivery Address), other (Other), private (Private)"
            in result
        )

    @pytest.mark.asyncio
    async def test_fields_with_numeric(
        self, resource_handler, mock_connection, mock_access_controller
    ):
        """Test fields with numeric types."""
        # Setup mocks
        mock_access_controller.validate_model_access.return_value = None
        mock_connection.fields_get.return_value = {
            "credit_limit": {"type": "float", "string": "Credit Limit", "digits": (16, 2)},
            "color": {"type": "integer", "string": "Color Index"},
            "debit": {"type": "monetary", "string": "Total Receivable", "readonly": True},
        }

        # Execute fields
        result = await resource_handler._handle_fields("account.partner")

        # Check numeric fields
        assert "FLOAT Fields (1):" in result
        assert "credit_limit:" in result
        assert "Precision: (16, 2)" in result

        assert "INTEGER Fields (1):" in result
        assert "color:" in result

        assert "MONETARY Fields (1):" in result
        assert "debit:" in result
        assert "Readonly: True" in result


class TestAdvancedResourceIntegration:
    """Integration tests for advanced resources with real Odoo."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    @skip_on_rate_limit
    async def test_count_real_records(self, real_config, real_connection):
        """Test count with real Odoo connection."""
        # Setup real components
        app = Mock(spec=FastMCP)
        app.resource = Mock()
        app._handlers = {}

        def resource_decorator(uri_pattern, **kwargs):
            def decorator(func):
                app._handlers[uri_pattern] = func
                return func

            return decorator

        app.resource.side_effect = resource_decorator

        access_controller = AccessController(real_config)
        handler = OdooResourceHandler(app, real_connection, access_controller, real_config)

        # Authenticate
        real_connection.connect()
        real_connection.authenticate()

        # Count all partners
        result_all = await handler._handle_count("res.partner", None)
        assert "Search criteria: All records" in result_all
        assert "Total count:" in result_all

        # Count companies only
        domain = [["is_company", "=", True]]
        result_companies = await handler._handle_count("res.partner", quote(json.dumps(domain)))
        assert "is_company = True" in result_companies
        assert "Total count:" in result_companies

    @pytest.mark.asyncio
    @pytest.mark.integration
    @skip_on_rate_limit
    async def test_fields_real_model(self, real_config, real_connection):
        """Test fields with real Odoo model."""
        # Setup real components
        app = Mock(spec=FastMCP)
        app.resource = Mock()
        app._handlers = {}

        def resource_decorator(uri_pattern, **kwargs):
            def decorator(func):
                app._handlers[uri_pattern] = func
                return func

            return decorator

        app.resource.side_effect = resource_decorator

        access_controller = AccessController(real_config)
        handler = OdooResourceHandler(app, real_connection, access_controller, real_config)

        # Authenticate
        real_connection.connect()
        real_connection.authenticate()

        # Get fields for res.partner
        result = await handler._handle_fields("res.partner")

        # Verify result contains expected fields
        assert "Field Definitions: res.partner" in result
        assert "Total fields:" in result

        # Check for common partner fields
        assert "name:" in result
        assert "email:" in result
        assert "is_company:" in result

        # Check field types are categorized
        assert "CHAR Fields" in result
        assert "BOOLEAN Fields" in result
        assert "MANY2ONE Fields" in result


@pytest.fixture
def real_config():
    """Load real configuration from .env file."""
    return load_config()


@pytest.fixture
def real_connection(real_config):
    """Create a real Odoo connection using the appropriate backend."""
    if real_config.api_version == "json2":
        from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection

        return OdooJSON2Connection(real_config)
    return OdooConnection(real_config)
