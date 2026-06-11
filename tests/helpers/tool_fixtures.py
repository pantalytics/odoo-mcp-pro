"""Shared fixtures for tool handler tests.

Used by tests/test_tools.py and tests/test_tools_crud.py.
"""

from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from mcp_server_odoo.access_control import AccessController
from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_connection import OdooConnection
from mcp_server_odoo.tools import OdooToolHandler


@pytest.fixture
def mock_app():
    """Create a mock FastMCP app."""
    app = MagicMock(spec=FastMCP)
    # Store registered tools
    app._tools = {}

    def tool_decorator(**kwargs):
        def decorator(func):
            # Store the function in our tools dict
            app._tools[func.__name__] = func
            return func

        return decorator

    app.tool = tool_decorator
    return app


@pytest.fixture
def mock_connection():
    """Create a mock OdooConnection."""
    connection = MagicMock(spec=OdooConnection)
    connection.is_authenticated = True
    return connection


@pytest.fixture
def mock_access_controller():
    """Create a mock AccessController."""
    controller = MagicMock(spec=AccessController)
    return controller


@pytest.fixture
def valid_config():
    """Create a valid config."""
    return OdooConfig(
        url="http://localhost:8069",
        api_key="test_api_key",
        database="test_db",
        default_limit=100,
        max_limit=500,
    )


@pytest.fixture
def handler(mock_app, mock_connection, mock_access_controller, valid_config):
    """Create an OdooToolHandler instance."""
    return OdooToolHandler(mock_app, mock_connection, mock_access_controller, valid_config)
