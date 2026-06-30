"""Tests for the optional per-call `connection` selector (Odoo task #758).

The public package only needs to ACCEPT and FORWARD an opaque selector to
`_get_user_context`; the hosted multi-tenant layer interprets it. In standalone
mode there is a single connection, so the selector is ignored. These tests pin
two contracts:

1. Every tool that resolves a connection forwards its `connection` argument
   (and None when omitted) to `_get_user_context`. Mutating tools also pass
   `writes=True` so the hosted layer can refuse an ambiguous write; read tools
   do not.
2. server_info exposes a `connections` list iff `_available_connections`
   returns one, and omits it (None) otherwise.

Shared tool fixtures live in tests/helpers/tool_fixtures.py.
"""

from unittest.mock import AsyncMock

import pytest

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


def _patch_user_context(handler, mock_connection, mock_access_controller):
    """Replace _get_user_context with an AsyncMock that returns a valid context.

    Returns the mock so the test can assert how it was awaited. The real Odoo
    work underneath is stubbed by the per-tool mocks below.
    """
    ctx = AsyncMock(return_value=(mock_connection, mock_access_controller, "stdio"))
    handler._get_user_context = ctx
    return ctx


class TestSelectorForwarding:
    """Each tool forwards the opaque `connection` selector verbatim."""

    @pytest.mark.asyncio
    async def test_search_records_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.search_count.return_value = 0
        mock_connection.search.return_value = []

        await mock_app._tools["search_records"](
            model="res.partner", fields=["name"], connection="7"
        )

        ctx.assert_awaited_once_with("7")

    @pytest.mark.asyncio
    async def test_search_records_defaults_to_none(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.search_count.return_value = 0
        mock_connection.search.return_value = []

        await mock_app._tools["search_records"](model="res.partner", fields=["name"])

        ctx.assert_awaited_once_with(None)

    @pytest.mark.asyncio
    async def test_get_record_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.read.return_value = [{"id": 1, "name": "x"}]

        await mock_app._tools["get_record"](
            model="res.partner", record_id=1, fields=["name"], connection="7"
        )

        ctx.assert_awaited_once_with("7")

    @pytest.mark.asyncio
    async def test_create_record_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.create.return_value = 1
        mock_connection.fields_get.return_value = {"id": {}, "name": {}}
        mock_connection.read.return_value = [{"id": 1, "name": "x"}]

        await mock_app._tools["create_record"](
            model="res.partner", values={"name": "x"}, connection="7"
        )

        ctx.assert_awaited_once_with("7", writes=True)

    @pytest.mark.asyncio
    async def test_update_record_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.read.return_value = [{"id": 1, "name": "x"}]
        mock_connection.write.return_value = True
        mock_connection.fields_get.return_value = {"id": {}, "name": {}}

        await mock_app._tools["update_record"](
            model="res.partner", record_id=1, values={"name": "y"}, connection="7"
        )

        ctx.assert_awaited_once_with("7", writes=True)

    @pytest.mark.asyncio
    async def test_delete_record_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.read.return_value = [{"id": 1, "name": "x"}]
        mock_connection.unlink.return_value = True

        await mock_app._tools["delete_record"](model="res.partner", record_id=1, connection="7")

        ctx.assert_awaited_once_with("7", writes=True)

    @pytest.mark.asyncio
    async def test_create_records_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.create_bulk.return_value = [1, 2]

        await mock_app._tools["create_records"](
            model="res.partner", vals_list=[{"name": "a"}, {"name": "b"}], connection="7"
        )

        ctx.assert_awaited_once_with("7", writes=True)

    @pytest.mark.asyncio
    async def test_update_records_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.write.return_value = True

        await mock_app._tools["update_records"](
            model="res.partner", record_ids=[1, 2], values={"name": "y"}, connection="7"
        )

        ctx.assert_awaited_once_with("7", writes=True)

    @pytest.mark.asyncio
    async def test_delete_records_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.unlink.return_value = True

        await mock_app._tools["delete_records"](
            model="res.partner", record_ids=[1, 2], connection="7"
        )

        ctx.assert_awaited_once_with("7", writes=True)

    @pytest.mark.asyncio
    async def test_import_records_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.load_records.return_value = {"ids": [1], "messages": []}

        await mock_app._tools["import_records"](
            model="res.partner",
            fields=["name"],
            data=[["Acme"]],
            connection="7",
        )

        ctx.assert_awaited_once_with("7", writes=True)

    @pytest.mark.asyncio
    async def test_list_models_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_access_controller.get_enabled_models.return_value = [
            {"model": "res.partner", "name": "Contact"}
        ]

        await mock_app._tools["list_models"](connection="7")

        ctx.assert_awaited_once_with("7")

    @pytest.mark.asyncio
    async def test_execute_method_forwards_selector(
        self, handler, mock_app, mock_connection, mock_access_controller
    ):
        ctx = _patch_user_context(handler, mock_connection, mock_access_controller)
        mock_connection.call_method.return_value = True

        await mock_app._tools["execute_method"](
            model="sale.order", method="action_confirm", ids=[1], connection="7"
        )

        ctx.assert_awaited_once_with("7", writes=True)


class TestServerInfoConnections:
    """server_info exposes `connections` iff the hook returns a list."""

    @pytest.mark.asyncio
    async def test_includes_connections_when_hook_returns_list(
        self, handler, mock_connection, mock_app
    ):
        mock_connection.database = "test_db"
        mock_connection.search_read.return_value = []
        conns = [
            {"id": "1", "url": "https://a.odoo.com", "db": "a"},
            {"id": "2", "url": "https://b.odoo.com", "db": "b"},
        ]
        handler._available_connections = AsyncMock(return_value=conns)

        result = await mock_app._tools["server_info"]()

        assert result.connections == conns

    @pytest.mark.asyncio
    async def test_omits_connections_when_hook_returns_none(
        self, handler, mock_connection, mock_app
    ):
        """Standalone: the base hook returns None, so the key stays absent."""
        mock_connection.database = "test_db"
        mock_connection.search_read.return_value = []

        result = await mock_app._tools["server_info"]()

        assert result.connections is None
