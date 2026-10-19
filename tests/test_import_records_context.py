"""import_records hands its context to Odoo's load() as given.

Element X (ticket #237) saw a `lang` import overwrite the base text. Odoo
does that itself when en_US is inactive (reproduced on Odoo 19 at the ORM),
so the connector's part is only this: `lang` reaches load() unchanged, and
keys that could escalate never do.
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

__all__ = [
    "handler",
    "mock_access_controller",
    "mock_app",
    "mock_connection",
    "valid_config",
]


@pytest.fixture
def ready(handler, mock_connection, mock_access_controller):
    handler._get_user_context = AsyncMock(
        return_value=(mock_connection, mock_access_controller, "stdio")
    )
    mock_connection.load_records.return_value = {"ids": [52], "messages": []}
    return mock_connection


@pytest.mark.asyncio
async def test_lang_reaches_load_unchanged(ready, mock_app):
    await mock_app._tools["import_records"](
        model="res.partner.category",
        fields=["id", "name"],
        data=[["__import__.zz_lang_test2", "ZZ lang test NL2"]],
        context={"lang": "nl_BE"},
    )

    args = ready.load_records.call_args.args
    assert args[0] == "res.partner.category"
    assert args[3] == {"lang": "nl_BE"}


@pytest.mark.asyncio
async def test_unsafe_context_key_is_refused(ready, mock_app):
    with pytest.raises(Exception) as exc:
        await mock_app._tools["import_records"](
            model="res.partner.category",
            fields=["id", "name"],
            data=[["__import__.x", "X"]],
            context={"lang": "nl_BE", "su": True},
        )

    assert "disallowed" in str(exc.value)
    ready.load_records.assert_not_called()
