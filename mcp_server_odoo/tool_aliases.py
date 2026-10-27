"""Tool names from before the 4.0.0 rename, still answered at call time.

4.0.0 named every tool `<object>_<verb>`. A client that keeps the tool list it
read when the connector was added (ChatGPT does until someone refreshes the
connector) goes on calling the old names, and every such call failed with
"Unknown tool". Each old name now runs the tool that replaced it, with the same
arguments. The rename changed names only, not parameters.

The old names are not listed: `tools/list` shows the current names only, so a
client that does read the list never sees two tools for one job.
"""

from __future__ import annotations

from typing import Any, Mapping

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


def accept_legacy_tool_names(app: Any, names: Mapping[str, str]) -> None:
    """Let `app` run each old name in `names` as the tool it maps to.

    Hooks the tool manager's lookup, which every call goes through (the SDK's
    dispatch, argument validation and any instrumentation that reads the
    schema), so an old name is the same tool in every respect. A registered
    tool always wins over an alias. Calling this again adds names; it never
    wraps the lookup twice.
    """
    manager = app._tool_manager
    aliases = getattr(manager, _ALIASES_ATTR, None)
    if aliases is None:
        aliases = {}
        lookup = manager.get_tool

        def get_tool(name: str):
            tool = lookup(name)
            if tool is None and name in aliases:
                tool = lookup(aliases[name])
            return tool

        manager.get_tool = get_tool
        setattr(manager, _ALIASES_ATTR, aliases)
    aliases.update(names)
