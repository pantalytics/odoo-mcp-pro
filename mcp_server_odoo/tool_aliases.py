"""Tool names from before the 4.0.0 rename, still answered at call time.

4.0.0 named every tool `<object>_<verb>`. A client that keeps the tool list it
read when the connector was added (ChatGPT does until someone refreshes the
connector) goes on calling the old names, and every such call failed with
"Unknown tool". Each old name now runs the tool that replaced it, with the same
arguments. The rename changed names only, not parameters.

The old names are not listed: `tools/list` shows the current names only, so a
client that does read the list never sees two tools for one job. The result of
an old-name call carries a one-line note with the new name.
"""

from __future__ import annotations

from typing import Any, Mapping

from mcp.types import CallToolResult, TextContent

# Old name -> current name. Frozen history: only an entry for a future rename
# is ever added here.
LEGACY_TOOL_NAMES: Mapping[str, str] = {
    "search_records": "record_search",
    "get_record": "record_get",
    "aggregate_records": "record_aggregate",
    "create_record": "record_create",
    "create_records": "records_create",
    "update_record": "record_update",
    "update_records": "records_update",
    "delete_record": "record_delete",
    "delete_records": "records_delete",
    "import_records": "record_import",
    "list_models": "model_list",
    "list_resource_templates": "resource_template_list",
    "server_info": "server_info_get",
    "set_binary_field": "binary_field_set",
    "post_message": "message_post",
    "execute_method": "method_execute",
    "register_payment": "payment_register",
    "find_skill": "skill_find",
    "get_skill": "skill_get",
}

_ALIASES_ATTR = "_legacy_tool_names"


def rename_hint(old: str, new: str) -> str:
    """The note an old-name call gets next to its result."""
    return (
        f"Note: the tool {old} is now called {new}. The old name still works, "
        "but this client has an old tool list. Ask the user to refresh the "
        "connector so the new names show up."
    )


def accept_legacy_tool_names(app: Any, names: Mapping[str, str]) -> None:
    """Let `app` run each old name in `names` as the tool it maps to.

    Hooks the tool manager's lookup, which every call goes through (the SDK's
    dispatch, argument validation and any instrumentation that reads the
    schema), so an old name is the same tool in every respect. A call by an
    old name also gets `rename_hint` as an extra text block. A registered tool
    always wins over an alias. Calling this again adds names; it never wraps
    twice.
    """
    manager = app._tool_manager
    aliases = getattr(manager, _ALIASES_ATTR, None)
    if aliases is None:
        aliases = {}
        lookup = manager.get_tool
        call = manager.call_tool

        def get_tool(name: str):
            tool = lookup(name)
            if tool is None and name in aliases:
                tool = lookup(aliases[name])
            return tool

        async def call_tool(name: str, arguments: Any, *args: Any, **kwargs: Any) -> Any:
            result = await call(name, arguments, *args, **kwargs)
            if name in aliases and lookup(name) is None and isinstance(result, CallToolResult):
                hint = TextContent(type="text", text=rename_hint(name, aliases[name]))
                result.content = [*result.content, hint]
            return result

        manager.get_tool = get_tool
        manager.call_tool = call_tool
        setattr(manager, _ALIASES_ATTR, aliases)
    aliases.update(names)
