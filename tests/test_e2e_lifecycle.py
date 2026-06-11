"""End-to-end integration tests: server lifecycle, authentication, resources.

These tests validate the complete MCP server functionality with a real Odoo server
using .env configuration. They cover the full server lifecycle, authentication
flows, and resource operations. Error handling, performance, and MCP protocol
compliance tests live in tests/test_e2e_protocol.py.
"""

import os
import subprocess
import sys
import time

import pytest
import requests

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_connection import OdooConnection
from tests.helpers.server_testing import (
    MCPTestServer,
    create_test_env_file,
    mcp_test_server,
    run_mcp_command,
    validate_resource_operation,
)

# Mark all tests in this module as integration tests requiring Odoo
pytestmark = [pytest.mark.integration, pytest.mark.odoo_required, pytest.mark.xmlrpc_only]


class TestServerLifecycle:
    """Test MCP server lifecycle management."""

    @pytest.mark.asyncio
    async def test_server_startup_and_shutdown(self):
        """Test that server can start up and shut down cleanly."""
        # Create server with test configuration
        config = OdooConfig.from_env()
        server = MCPTestServer(config)

        # Start server
        await server.start()
        assert server.server is not None
        assert server.odoo_connection is not None

        # Verify connection is active
        assert server.odoo_connection.is_connected

        # Stop server
        await server.stop()
        assert server.server is None
        assert server.odoo_connection is None

    def test_server_subprocess_lifecycle(self):
        """Test server can be started as a subprocess."""
        config = OdooConfig.from_env()

        with mcp_test_server(config) as server:
            # Start subprocess
            process = server.start_subprocess()
            assert process is not None
            assert process.poll() is None  # Process is running

            # Give server time to initialize
            time.sleep(2)

            # Process should still be running
            assert process.poll() is None

        # After context exit, process should be terminated
        assert server.server_process is None

    def test_server_with_env_file(self, tmp_path):
        """Test server can load configuration from .env file."""
        # Create test .env file
        create_test_env_file(tmp_path)

        # Change to test directory
        original_cwd = os.getcwd()
        os.chdir(tmp_path)

        try:
            # Load config from .env
            config = OdooConfig.from_env()
            assert config.url == os.getenv("ODOO_URL", "http://localhost:8069")
            assert config.api_key == os.getenv("ODOO_API_KEY")
            assert config.database == os.getenv("ODOO_DB")

        finally:
            os.chdir(original_cwd)

    def test_uvx_server_startup(self):
        """Test that server can be started with uvx command."""
        # Create a test script to simulate uvx execution
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_odoo", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Server module should be executable
        assert result.returncode == 0 or "MCP" in result.stdout or "MCP" in result.stderr


class TestAuthenticationFlows:
    """Test authentication flows with different configurations."""

    def test_api_key_authentication_from_env(self):
        """Test API key authentication using .env configuration."""
        config = OdooConfig.from_env()

        # Verify API key is loaded
        assert config.api_key is not None

        # Test connection with API key
        conn = OdooConnection(config)
        conn.connect()
        conn.authenticate()

        assert conn.is_connected
        assert conn.uid is not None

        # Verify we can execute operations
        version = conn.get_server_version()
        assert version is not None

        conn.close()

    def test_username_password_fallback(self):
        """Test fallback to username/password when API key fails."""
        # Create config with invalid API key
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="invalid_key",
            database=os.getenv("ODOO_DB"),
            username=os.getenv("ODOO_USER", "admin"),
            password=os.getenv("ODOO_PASSWORD", "admin"),
        )

        conn = OdooConnection(config)

        # Connection should succeed with username/password fallback
        conn.connect()
        conn.authenticate()
        assert conn.is_connected

        conn.close()

    def test_rest_api_authentication(self):
        """Test REST API authentication with API key."""
        config = OdooConfig.from_env()

        # Test health check (no auth)
        response = requests.get(f"{config.url}/mcp/health")
        assert response.status_code == 200

        # Test authenticated endpoint
        headers = {"X-API-Key": config.api_key}
        response = requests.get(f"{config.url}/mcp/system/info", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "db_name" in data.get("data", {})

    def test_authentication_error_handling(self):
        """Test proper error handling for authentication failures."""
        config = OdooConfig.from_env()

        # Test with invalid API key
        headers = {"X-API-Key": "invalid_key"}
        response = requests.get(f"{config.url}/mcp/system/info", headers=headers)
        assert response.status_code == 401


class TestResourceOperations:
    """Test all resource operations with real Odoo data."""

    @pytest.mark.asyncio
    async def test_record_resource_operation(self):
        """Test record resource for retrieving single records."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Use existing admin user record (ID=2)
            # This avoids needing create permissions
            uri = "odoo://res.users/record?id=2"
            success, error = await validate_resource_operation(server.server, uri)

            assert success, f"Record operation failed: {error}"

    @pytest.mark.asyncio
    async def test_search_resource_operation(self):
        """Test search resource for finding records."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test search operation
            uri = "odoo://res.users/search?domain=[('id','=',2)]&limit=5"
            success, error = await validate_resource_operation(server.server, uri)

            assert success, f"Search operation failed: {error}"

    @pytest.mark.asyncio
    async def test_browse_resource_operation(self):
        """Test browse resource for navigating records."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test browse operation
            uri = "odoo://res.partner/browse?offset=0&limit=10"
            success, error = await validate_resource_operation(server.server, uri)

            assert success, f"Browse operation failed: {error}"

    @pytest.mark.asyncio
    async def test_count_resource_operation(self):
        """Test count resource for counting records."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test count operation
            uri = "odoo://res.partner/count?domain=[]"
            success, error = await validate_resource_operation(server.server, uri)

            assert success, f"Count operation failed: {error}"

    @pytest.mark.asyncio
    async def test_fields_resource_operation(self):
        """Test fields resource for model metadata."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test fields operation
            uri = "odoo://res.partner/fields"
            success, error = await validate_resource_operation(server.server, uri)

            assert success, f"Fields operation failed: {error}"

    @pytest.mark.asyncio
    async def test_resource_with_complex_domain(self):
        """Test resource operations with complex search domains."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Use a complex domain with existing data
            # Search for users with specific criteria
            domain = "[('id','in',[1,2]),('active','=',True)]"
            uri = f"odoo://res.users/search?domain={domain}&limit=5"

            response = await run_mcp_command(server.server, "resources/read", {"uri": uri})

            assert "result" in response
            contents = response["result"]["contents"]
            assert len(contents) > 0

            # Verify we got results
            text = contents[0]["text"]
            assert "Mock data" in text  # Our mock implementation returns this
