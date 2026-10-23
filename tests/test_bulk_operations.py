"""Tests for bulk CRUD operations."""

from unittest.mock import MagicMock

import pytest

from mcp_server_odoo.tools import MAX_BULK_SIZE, OdooToolHandler


@pytest.fixture
def mock_connection():
    """Create a mock Odoo connection."""
    conn = MagicMock()
    conn.is_authenticated = True
    conn.is_connected = True
    return conn


@pytest.fixture
def mock_access_controller():
    """Create a mock access controller that allows everything."""
    ac = MagicMock()
    ac.validate_model_access = MagicMock()
    return ac


@pytest.fixture
def handler(mock_connection, mock_access_controller):
    """Create OdooToolHandler with mocked dependencies."""
    app = MagicMock()
    app.tool = MagicMock(return_value=lambda f: f)
    h = OdooToolHandler(
        app=app,
        connection=mock_connection,
        access_controller=mock_access_controller,
    )
    return h


class TestBulkCreate:
    @pytest.mark.asyncio
    async def test_create_records_success(self, handler, mock_connection):
        mock_connection.create_bulk.return_value = [1, 2, 3]

        result = await handler._handle_create_records_tool(
            "res.partner",
            [{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}],
        )

        assert result["success"] is True
        assert result["created_ids"] == [1, 2, 3]
        assert result["count"] == 3
        assert result["model"] == "res.partner"
        mock_connection.create_bulk.assert_called_once_with(
            "res.partner",
            [{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}],
        )

    @pytest.mark.asyncio
    async def test_create_records_single(self, handler, mock_connection):
        """Single-item list should still work."""
        mock_connection.create_bulk.return_value = [42]

        result = await handler._handle_create_records_tool("res.partner", [{"name": "Solo"}])

        assert result["created_ids"] == [42]
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_create_records_empty_list(self, handler):
        from mcp_server_odoo.error_handling import ValidationError

        with pytest.raises(ValidationError, match="cannot be empty"):
            await handler._handle_create_records_tool("res.partner", [])

    @pytest.mark.asyncio
    async def test_create_records_exceeds_max(self, handler):
        from mcp_server_odoo.error_handling import ValidationError

        too_many = [{"name": f"record-{i}"} for i in range(MAX_BULK_SIZE + 1)]
        with pytest.raises(ValidationError, match="limited to"):
            await handler._handle_create_records_tool("res.partner", too_many)

    @pytest.mark.asyncio
    async def test_create_records_at_max(self, handler, mock_connection):
        """Exactly MAX_BULK_SIZE should be allowed."""
        mock_connection.create_bulk.return_value = list(range(1, MAX_BULK_SIZE + 1))

        vals = [{"name": f"record-{i}"} for i in range(MAX_BULK_SIZE)]
        result = await handler._handle_create_records_tool("res.partner", vals)

        assert result["count"] == MAX_BULK_SIZE

    @pytest.mark.asyncio
    async def test_create_records_access_denied(self, handler, mock_access_controller):
        from mcp_server_odoo.access_control import AccessControlError
        from mcp_server_odoo.error_handling import ValidationError

        mock_access_controller.validate_model_access.side_effect = AccessControlError("Not allowed")

        with pytest.raises(ValidationError, match="AccessError"):
            await handler._handle_create_records_tool("res.partner", [{"name": "test"}])


class TestBulkUpdate:
    @pytest.mark.asyncio
    async def test_update_records_success(self, handler, mock_connection):
        mock_connection.write.return_value = True

        result = await handler._handle_update_records_tool(
            "res.partner", [1, 2, 3], {"is_company": True}
        )

        assert result["success"] is True
        assert result["updated_ids"] == [1, 2, 3]
        assert result["count"] == 3
        mock_connection.write.assert_called_once_with(
            "res.partner", [1, 2, 3], {"is_company": True}
        )

    @pytest.mark.asyncio
    async def test_update_records_empty_ids(self, handler):
        from mcp_server_odoo.error_handling import ValidationError

        with pytest.raises(ValidationError, match="cannot be empty"):
            await handler._handle_update_records_tool("res.partner", [], {"name": "test"})

    @pytest.mark.asyncio
    async def test_update_records_empty_values(self, handler):
        from mcp_server_odoo.error_handling import ValidationError

        with pytest.raises(ValidationError, match="cannot be empty"):
            await handler._handle_update_records_tool("res.partner", [1, 2], {})

    @pytest.mark.asyncio
    async def test_update_records_exceeds_max(self, handler):
        from mcp_server_odoo.error_handling import ValidationError

        too_many = list(range(MAX_BULK_SIZE + 1))
        with pytest.raises(ValidationError, match="limited to"):
            await handler._handle_update_records_tool("res.partner", too_many, {"name": "test"})


class TestBulkDelete:
    @pytest.mark.asyncio
    async def test_delete_records_success(self, handler, mock_connection):
        mock_connection.search.return_value = [10, 11, 12]
        mock_connection.unlink.return_value = True

        result = await handler._handle_delete_records_tool("res.partner", [10, 11, 12])

        assert result["success"] is True
        assert result["deleted_ids"] == [10, 11, 12]
        assert result["count"] == 3
        mock_connection.unlink.assert_called_once_with("res.partner", [10, 11, 12])

    @pytest.mark.asyncio
    async def test_delete_records_empty_ids(self, handler):
        from mcp_server_odoo.error_handling import ValidationError

        with pytest.raises(ValidationError, match="cannot be empty"):
            await handler._handle_delete_records_tool("res.partner", [])

    @pytest.mark.asyncio
    async def test_delete_records_exceeds_max(self, handler):
        from mcp_server_odoo.error_handling import ValidationError

        too_many = list(range(MAX_BULK_SIZE + 1))
        with pytest.raises(ValidationError, match="limited to"):
            await handler._handle_delete_records_tool("res.partner", too_many)


class TestBulkCreateJSON2Connection:
    """Test create_bulk on the JSON/2 connection."""

    def test_create_bulk_sends_vals_list(self):
        from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection

        conn = MagicMock(spec=OdooJSON2Connection)
        conn._call = MagicMock(return_value=[1, 2])
        conn._fields_cache = {}

        # Call the real method
        result = OdooJSON2Connection.create_bulk(
            conn, "res.partner", [{"name": "A"}, {"name": "B"}]
        )

        conn._call.assert_called_once_with(
            "res.partner", "create", vals_list=[{"name": "A"}, {"name": "B"}]
        )
        assert result == [1, 2]

    def test_create_bulk_wraps_single_result(self):
        from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection

        conn = MagicMock(spec=OdooJSON2Connection)
        conn._call = MagicMock(return_value=42)  # Non-list result
        conn._fields_cache = {}

        result = OdooJSON2Connection.create_bulk(conn, "res.partner", [{"name": "A"}])

        assert result == [42]


class TestMaxBulkSize:
    def test_max_bulk_size_is_1000(self):
        assert MAX_BULK_SIZE == 1000
