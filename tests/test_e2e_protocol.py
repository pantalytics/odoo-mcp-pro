"""End-to-end integration tests: errors, performance, MCP protocol compliance.

These tests validate the complete MCP server functionality with a real Odoo server
using .env configuration. They cover error handling, performance and reliability,
MCP protocol compliance, and end-to-end workflows. Server lifecycle, authentication,
and resource operation tests live in tests/test_e2e_lifecycle.py.
"""

import asyncio
import json
from typing import Any, Dict

import pytest

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_connection import OdooConnection
from tests.helpers.server_testing import (
    MCPTestServer,
    PerformanceTimer,
    assert_performance,
    check_odoo_health,
    run_mcp_command,
    validate_mcp_response,
    validate_resource_operation,
)

# Mark all tests in this module as integration tests requiring Odoo
pytestmark = [pytest.mark.integration, pytest.mark.odoo_required, pytest.mark.xmlrpc_only]


class TestErrorHandling:
    """Test error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_invalid_model_error(self):
        """Test error handling for invalid model names."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test with non-existent model
            uri = "odoo://invalid.model/search?domain=[]"
            success, error = await validate_resource_operation(server.server, uri)

            assert not success
            assert "access" in error.lower() or "not found" in error.lower()

    @pytest.mark.asyncio
    async def test_access_denied_error(self):
        """Test error handling for access denied scenarios."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test with a model that might not be MCP-enabled
            uri = "odoo://ir.config_parameter/search?domain=[]"
            response = await run_mcp_command(server.server, "resources/read", {"uri": uri})

            # Should either succeed or return proper error
            assert validate_mcp_response(response)

    @pytest.mark.asyncio
    async def test_invalid_uri_format_error(self):
        """Test error handling for malformed URIs."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test various invalid URIs
            invalid_uris = [
                "invalid://format",
                "odoo://",
                "odoo://model/invalid_operation",
                "odoo://res.partner/search?invalid_param=test",
            ]

            for uri in invalid_uris:
                response = await run_mcp_command(server.server, "resources/read", {"uri": uri})

                assert "error" in response

    def test_connection_failure_recovery(self):
        """Test recovery from connection failures."""
        config = OdooConfig.from_env()
        conn = OdooConnection(config)

        # Connect initially
        conn.connect()
        conn.authenticate()
        assert conn.is_connected

        # Simulate connection loss by closing
        conn.close()
        assert not conn.is_connected

        # Need to manually reconnect
        conn.connect()
        conn.authenticate()
        version = conn.get_server_version()
        assert version is not None
        assert conn.is_connected

        conn.close()

    @pytest.mark.asyncio
    async def test_large_result_handling(self):
        """Test handling of large result sets."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Request large number of records
            uri = "odoo://res.partner/browse?limit=1000"

            with PerformanceTimer("Large result fetch"):
                response = await run_mcp_command(server.server, "resources/read", {"uri": uri})

            assert "result" in response
            # Should handle gracefully, possibly with pagination info


class TestPerformanceAndReliability:
    """Test performance and reliability aspects."""

    @pytest.mark.asyncio
    async def test_connection_reuse(self):
        """Test that connections are properly reused."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()
            conn = server.odoo_connection

            # Perform multiple operations
            for _ in range(5):
                version = conn.get_server_version()
                assert version is not None

            # Connection should be reused
            assert conn.is_connected

    @pytest.mark.asyncio
    async def test_operation_performance(self):
        """Test that operations complete within acceptable time."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test various operations with timing
            operations = [
                ("Record fetch", "odoo://res.users/record?id=2", 1.0),
                ("Small search", "odoo://res.partner/search?limit=10", 1.0),
                ("Field metadata", "odoo://res.partner/fields", 2.0),
                ("Count operation", "odoo://res.partner/count", 1.0),
            ]

            for op_name, uri, max_time in operations:
                with PerformanceTimer(op_name) as timer:
                    response = await run_mcp_command(server.server, "resources/read", {"uri": uri})

                assert "result" in response
                assert_performance(op_name, timer.elapsed, max_time)

    @pytest.mark.asyncio
    async def test_concurrent_operations(self):
        """Test handling of concurrent operations."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Define concurrent tasks
            async def fetch_resource(uri: str) -> Dict[str, Any]:
                return await run_mcp_command(server.server, "resources/read", {"uri": uri})

            # Run multiple operations concurrently
            uris = [
                "odoo://res.users/record?id=2",
                "odoo://res.partner/count",
                "odoo://res.partner/fields",
                "odoo://res.users/search?limit=5",
            ]

            with PerformanceTimer("Concurrent operations"):
                results = await asyncio.gather(*[fetch_resource(uri) for uri in uris])

            # All operations should succeed
            for result in results:
                assert validate_mcp_response(result)
                assert "result" in result

    def test_server_health_monitoring(self):
        """Test server health check functionality."""
        config = OdooConfig.from_env()

        # Check Odoo health
        is_healthy = check_odoo_health(config.url, config.api_key)
        assert is_healthy

        # Test with invalid credentials
        is_healthy = check_odoo_health(config.url, "invalid_key")
        assert not is_healthy


class TestMCPProtocolCompliance:
    """Test MCP protocol compliance and integration."""

    @pytest.mark.asyncio
    async def test_resource_list_operation(self):
        """Test MCP resources/list operation."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # List available resources
            response = await run_mcp_command(server.server, "resources/list", {})

            assert "result" in response
            resources = response["result"]["resources"]
            assert isinstance(resources, list)

            # Should have schema resources
            schema_resources = [r for r in resources if "schema" in r["uri"]]
            assert len(schema_resources) == 5  # One for each operation

    @pytest.mark.asyncio
    async def test_mcp_response_format(self):
        """Test that all responses follow MCP format."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test successful response
            response = await run_mcp_command(
                server.server, "resources/read", {"uri": "odoo://res.users/record?id=2"}
            )

            assert validate_mcp_response(response)
            assert "result" in response

            result = response["result"]
            assert "contents" in result
            assert isinstance(result["contents"], list)

            for content in result["contents"]:
                assert "uri" in content
                assert "mimeType" in content
                assert content["mimeType"] == "text/plain"
                assert "text" in content

    @pytest.mark.asyncio
    async def test_schema_resources(self):
        """Test that schema resources are properly served."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test each schema resource
            schema_uris = [
                "odoo://schema/record",
                "odoo://schema/search",
                "odoo://schema/browse",
                "odoo://schema/count",
                "odoo://schema/fields",
            ]

            for uri in schema_uris:
                response = await run_mcp_command(server.server, "resources/read", {"uri": uri})

                assert "result" in response
                contents = response["result"]["contents"]
                assert len(contents) > 0

                # Schema should be in JSON format
                schema_text = contents[0]["text"]
                schema = json.loads(schema_text)

                # Validate schema structure
                assert "operation" in schema
                assert "parameters" in schema
                assert "description" in schema


class TestEndToEndWorkflow:
    """Test complete end-to-end workflows."""

    @pytest.mark.asyncio
    async def test_read_workflow(self):
        """Test read operations workflow with existing data."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Test reading existing user record
            uri = "odoo://res.users/record?id=2"
            response = await run_mcp_command(server.server, "resources/read", {"uri": uri})

            assert "result" in response
            text = response["result"]["contents"][0]["text"]
            assert "Mock data" in text  # Our mock returns this

            # Test search operation
            search_uri = "odoo://res.users/search?domain=[('id','=',2)]"
            response = await run_mcp_command(server.server, "resources/read", {"uri": search_uri})

            assert "result" in response

            # Test browse operation
            browse_uri = "odoo://res.users/browse?limit=5"
            response = await run_mcp_command(server.server, "resources/read", {"uri": browse_uri})

            assert "result" in response

    @pytest.mark.asyncio
    async def test_relationship_navigation_workflow(self):
        """Test navigating relationships between models."""
        config = OdooConfig.from_env()

        async with MCPTestServer(config) as server:
            await server.start()

            # Start with a user
            user_uri = "odoo://res.users/record?id=2"
            response = await run_mcp_command(server.server, "resources/read", {"uri": user_uri})

            assert "result" in response
            # user_text = response["result"]["contents"][0]["text"]

            # Extract partner_id from response (simplified)
            # In real implementation, would parse the formatted text

            # Navigate to related partner
            partner_uri = "odoo://res.partner/search?domain=[('user_ids','=',2)]"
            response = await run_mcp_command(server.server, "resources/read", {"uri": partner_uri})

            assert "result" in response
            assert len(response["result"]["contents"]) > 0
