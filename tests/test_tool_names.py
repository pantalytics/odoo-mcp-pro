"""Every tool name is `<object>_<verb>`: lowercase words, the last one a verb
from VERBS (pantalytics/odoo-mcp-pro-admin#528). A new tool with a new verb
adds it here on purpose; a verb-first name like `search_records` fails."""

import asyncio
import re

from mcp_server_odoo.server import create_fastmcp_app
from mcp_server_odoo.tools import OdooToolHandler

VERBS = {
    "aggregate",
    "create",
    "delete",
    "execute",
    "export",
    "find",
    "get",
    "import",
    "list",
    "post",
    "register",
    "search",
    "set",
    "update",
}
_SHAPE = re.compile(r"^[a-z]+(_[a-z]+)+$")


def _tool_names():
    app = create_fastmcp_app()
    OdooToolHandler(app)
    return [t.name for t in asyncio.run(app.list_tools())]


def test_every_tool_is_object_then_verb():
    names = _tool_names()
    assert names, "no tools registered"
    wrong = [n for n in names if not _SHAPE.match(n) or n.rsplit("_", 1)[1] not in VERBS]
    assert not wrong, f"not <object>_<verb>: {wrong}"


def test_the_renamed_tools_are_the_ones_registered():
    names = set(_tool_names())
    assert {"record_search", "record_create", "message_post", "skill_find"} <= names
    assert not names & {"search_records", "create_record", "post_message", "find_skill"}
