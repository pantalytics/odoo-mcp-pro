"""Tests for describe_model tool."""

from unittest.mock import Mock

import pytest

from mcp_server_odoo.access_control import AccessControlError
from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.odoo_connection import OdooConnectionError
from mcp_server_odoo.schemas import DescribeModelResult, FieldInfo
from mcp_server_odoo.tools import OdooToolHandler


@pytest.fixture
def mock_app():
    app = Mock()
    app.tool = Mock(side_effect=lambda **kwargs: lambda func: func)
    return app


@pytest.fixture
def mock_connection():
    conn = Mock()
    conn.is_authenticated = True
    return conn


@pytest.fixture
def mock_access_controller():
    controller = Mock()
    controller.validate_model_access = Mock()
    return controller


@pytest.fixture
def mock_config():
    config = Mock()
    config.default_limit = 10
    config.max_limit = 100
    config.url = "http://localhost:8069"
    return config


@pytest.fixture
def tool_handler(mock_app, mock_connection, mock_access_controller, mock_config):
    return OdooToolHandler(mock_app, mock_connection, mock_access_controller, mock_config)


class TestDescribeModelTool:
    """Tests for describe_model tool."""

    @pytest.mark.asyncio
    async def test_describe_model_success(self, tool_handler, mock_connection):
        """Basic success: fields_get result mapped to DescribeModelResult."""
        mock_connection.fields_get.return_value = {
            "name": {"type": "char", "string": "Name", "required": True, "readonly": False, "relation": None, "help": ""},
            "tag_ids": {"type": "many2many", "string": "Tags", "required": False, "readonly": False, "relation": "res.partner.category", "help": ""},
        }
        result = await tool_handler._handle_describe_model_tool("res.partner")
        assert isinstance(result, DescribeModelResult)
        assert result.model == "res.partner"
        assert result.total_fields == 2

    @pytest.mark.asyncio
    async def test_is_m2m_flag_set_for_many2many(self, tool_handler, mock_connection):
        """is_m2m is True only for many2many fields."""
        mock_connection.fields_get.return_value = {
            "tag_ids": {"type": "many2many", "string": "Tags", "required": False, "readonly": False, "relation": "res.partner.category", "help": ""},
            "user_id": {"type": "many2one", "string": "Salesperson", "required": False, "readonly": False, "relation": "res.users", "help": ""},
        }
        result = await tool_handler._handle_describe_model_tool("crm.lead")
        assert result.fields["tag_ids"].is_m2m is True
        assert result.fields["user_id"].is_m2m is False

    @pytest.mark.asyncio
    async def test_empty_help_becomes_none(self, tool_handler, mock_connection):
        """Empty string help from Odoo is converted to None."""
        mock_connection.fields_get.return_value = {
            "name": {"type": "char", "string": "Name", "required": False, "readonly": False, "relation": None, "help": ""},
        }
        result = await tool_handler._handle_describe_model_tool("res.partner")
        assert result.fields["name"].help is None

    @pytest.mark.asyncio
    async def test_non_empty_help_preserved(self, tool_handler, mock_connection):
        """Non-empty help text is preserved."""
        mock_connection.fields_get.return_value = {
            "vat": {"type": "char", "string": "Tax ID", "required": False, "readonly": False, "relation": None, "help": "Tax identification number"},
        }
        result = await tool_handler._handle_describe_model_tool("res.partner")
        assert result.fields["vat"].help == "Tax identification number"

    @pytest.mark.asyncio
    async def test_default_attributes_used_when_none(self, tool_handler, mock_connection):
        """Default attributes are passed to fields_get when attributes=None."""
        mock_connection.fields_get.return_value = {}
        await tool_handler._handle_describe_model_tool("res.partner", None)
        mock_connection.fields_get.assert_called_once_with(
            "res.partner",
            ["string", "type", "required", "readonly", "relation", "help"],
        )

    @pytest.mark.asyncio
    async def test_custom_attributes_passed_through(self, tool_handler, mock_connection):
        """Custom attributes list is passed directly to fields_get."""
        mock_connection.fields_get.return_value = {}
        await tool_handler._handle_describe_model_tool("res.partner", ["string", "type"])
        mock_connection.fields_get.assert_called_once_with("res.partner", ["string", "type"])

    @pytest.mark.asyncio
    async def test_access_control_called(self, tool_handler, mock_access_controller, mock_connection):
        """validate_model_access is called with model and 'read'."""
        mock_connection.fields_get.return_value = {}
        await tool_handler._handle_describe_model_tool("res.partner")
        mock_access_controller.validate_model_access.assert_called_once_with("res.partner", "read")

    @pytest.mark.asyncio
    async def test_access_control_error_raises_validation_error(self, tool_handler, mock_access_controller):
        """AccessControlError is converted to ValidationError."""
        mock_access_controller.validate_model_access.side_effect = AccessControlError("No access")
        with pytest.raises(ValidationError, match="No access"):
            await tool_handler._handle_describe_model_tool("res.partner")

    @pytest.mark.asyncio
    async def test_not_authenticated_raises_validation_error(self, tool_handler, mock_connection):
        """Not authenticated raises ValidationError."""
        mock_connection.is_authenticated = False
        with pytest.raises(ValidationError, match="Not authenticated"):
            await tool_handler._handle_describe_model_tool("res.partner")

    @pytest.mark.asyncio
    async def test_connection_error_raises_validation_error(self, tool_handler, mock_connection):
        """OdooConnectionError is converted to ValidationError."""
        mock_connection.fields_get.side_effect = OdooConnectionError("Connection failed")
        with pytest.raises(ValidationError, match="Connection error"):
            await tool_handler._handle_describe_model_tool("res.partner")

    @pytest.mark.asyncio
    async def test_total_fields_count(self, tool_handler, mock_connection):
        """total_fields matches the number of returned fields."""
        mock_connection.fields_get.return_value = {
            f"field_{i}": {"type": "char", "string": f"Field {i}", "required": False, "readonly": False, "relation": None, "help": ""}
            for i in range(5)
        }
        result = await tool_handler._handle_describe_model_tool("res.partner")
        assert result.total_fields == 5

    @pytest.mark.asyncio
    async def test_empty_relation_becomes_none(self, tool_handler, mock_connection):
        """Empty string relation from Odoo is converted to None."""
        mock_connection.fields_get.return_value = {
            "partner_id": {"type": "many2one", "string": "Partner", "required": False, "readonly": False, "relation": "", "help": ""},
        }
        result = await tool_handler._handle_describe_model_tool("sale.order")
        assert result.fields["partner_id"].relation is None

    @pytest.mark.asyncio
    async def test_none_help_from_odoo_becomes_none(self, tool_handler, mock_connection):
        """None help from Odoo (null in JSON) stays None."""
        mock_connection.fields_get.return_value = {
            "name": {"type": "char", "string": "Name", "required": False, "readonly": False, "relation": None, "help": None},
        }
        result = await tool_handler._handle_describe_model_tool("res.partner")
        assert result.fields["name"].help is None
