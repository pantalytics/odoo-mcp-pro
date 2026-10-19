"""What the model is told when a call fails or does less than asked.

Each case here used to come back as success, or as a message that sent the
caller the wrong way (the 2026-10 error audit after ticket #237).
"""

import pytest

from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.exceptions import (
    OdooConnectionError,
    OdooExecutionError,
    OdooTimeoutError,
)
from mcp_server_odoo.tools._common import parse_domain
from tests.helpers.tool_fixtures import (
    handler,
    mock_access_controller,
    mock_app,
    mock_connection,
    valid_config,
)

__all__ = [
    "handler",
    "mock_access_controller",
    "mock_app",
    "mock_connection",
    "valid_config",
]


class TestImportRecords:
    @pytest.mark.asyncio
    async def test_warnings_are_returned(self, handler, mock_connection):
        mock_connection.load_records.return_value = {
            "ids": [7],
            "messages": [
                {
                    "type": "warning",
                    "rows": {"from": 0, "to": 0},
                    "field": "parent_id",
                    "message": "Found multiple matches for value 'Acme' in field 'Parent'",
                }
            ],
        }
        result = await handler._handle_import_records_tool(
            "res.partner", ["name", "parent_id"], [["Bob", "Acme"]]
        )
        assert result["success"] is True
        assert result["warnings"] == [
            {
                "row": 0,
                "type": "warning",
                "field": "parent_id",
                "message": "Found multiple matches for value 'Acme' in field 'Parent'",
            }
        ]
        assert "1 warning(s)" in result["message"]

    @pytest.mark.asyncio
    async def test_any_error_says_the_batch_rolled_back(self, handler, mock_connection):
        mock_connection.load_records.return_value = {
            "ids": False,
            "messages": [
                {
                    "type": "error",
                    "rows": {"from": 1, "to": 1},
                    "field": "email",
                    "message": "Invalid email",
                }
            ],
        }
        result = await handler._handle_import_records_tool(
            "res.partner", ["name", "email"], [["A", "a@x.nl"], ["B", "bad"]]
        )
        assert result["success"] is False
        assert result["imported"] == 0
        assert result["errors"][0]["field"] == "email"
        assert "rolls back all 2 row(s)" in result["message"]


class TestDeleteRecords:
    @pytest.mark.asyncio
    async def test_missing_ids_are_named_and_nothing_is_deleted(self, handler, mock_connection):
        mock_connection.search.return_value = [10]
        with pytest.raises(ValidationError, match=r"id\(s\) \[11, 12\].*Nothing was deleted"):
            await handler._handle_delete_records_tool("res.partner", [10, 11, 12])
        mock_connection.unlink.assert_not_called()


class TestExecuteMethod:
    @pytest.mark.asyncio
    async def test_unencodable_return_says_it_ran(self, handler, mock_connection):
        mock_connection.call_method.side_effect = OdooExecutionError(
            "Operation failed: TypeError: cannot marshal <class 'odoo.api.account.move'> objects"
        )
        with pytest.raises(ValidationError, match="ran in Odoo.*Do not call it again"):
            await handler._handle_execute_method_tool("account.move", "action_post", ids=[3])

    @pytest.mark.asyncio
    async def test_timeout_on_a_write_says_outcome_unknown(self, handler, mock_connection):
        mock_connection.call_method.side_effect = OdooTimeoutError(
            "Request timeout after 30s: account.move/action_post"
        )
        with pytest.raises(ValidationError, match="Outcome unknown.*may have applied"):
            await handler._handle_execute_method_tool("account.move", "action_post", ids=[3])

    @pytest.mark.asyncio
    async def test_ids_inside_kwargs_are_refused(self, handler, mock_connection):
        with pytest.raises(ValidationError, match="ids parameter, not inside kwargs"):
            await handler._handle_execute_method_tool(
                "res.partner", "write", kwargs={"ids": [1], "vals": {"name": "X"}}
            )
        mock_connection.call_method.assert_not_called()


class TestReads:
    @pytest.mark.asyncio
    async def test_timeout_on_a_read_is_a_connection_error(self, handler, mock_connection):
        mock_connection.search_count.side_effect = OdooTimeoutError("Request timeout after 30s")
        with pytest.raises(ValidationError, match="^Connection error"):
            await handler._handle_search_tool("res.partner", [], None, 10, 0, None)

    @pytest.mark.asyncio
    async def test_list_models_failure_is_not_an_empty_list(
        self, handler, mock_connection, mock_access_controller
    ):
        mock_access_controller.get_enabled_models.return_value = []
        mock_connection.search_read.side_effect = OdooConnectionError("Server error (502)")
        with pytest.raises(ValidationError):
            await handler._handle_list_models_tool()


class TestParseDomain:
    def test_python_style_values_survive(self):
        assert parse_domain("[('name', '=', \"O'Brien\")]") == [("name", "=", "O'Brien")]
        assert parse_domain("[('name', '=', 'True Value Inc'), ('active', '=', True)]") == [
            ("name", "=", "True Value Inc"),
            ("active", "=", True),
        ]

    def test_bad_domain_shows_the_expected_shape(self):
        with pytest.raises(ValidationError, match="Expected a list of conditions"):
            parse_domain("[('name', '=' 'x')")
