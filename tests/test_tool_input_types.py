"""Filter and field inputs advertise one concrete type (odoo-mcp-pro-admin#661).

Microsoft Copilot Studio maps every tool input to one Power Fx type and drops
an untyped input or a union, so its agents searched without a domain. The
schema now says `string` for a domain and `array of string` for fields, with
no null default. Validation still takes what clients sent before: a list, a
JSON string or nothing, so Claude, ChatGPT and older prompts keep working.
"""

import asyncio

import pytest

from mcp_server_odoo.server import create_fastmcp_app
from mcp_server_odoo.tools import OdooToolHandler

DOMAIN_TOOLS = ("record_search", "record_aggregate", "record_export")
FIELDS_TOOLS = ("record_search", "record_get", "record_export")


@pytest.fixture(scope="module")
def app():
    app = create_fastmcp_app()
    OdooToolHandler(app)
    return app


def _props(app, name):
    tool = next(t for t in asyncio.run(app.list_tools()) if t.name == name)
    return tool.input_schema["properties"], tool.input_schema.get("required", [])


@pytest.mark.parametrize("name", DOMAIN_TOOLS)
def test_domain_is_one_string_type_and_optional(app, name):
    props, required = _props(app, name)
    domain = props["domain"]
    assert domain["type"] == "string"
    assert not {"anyOf", "oneOf", "default"} & set(domain)
    assert "JSON" in domain["description"]
    assert "domain" not in required


@pytest.mark.parametrize("name", FIELDS_TOOLS)
def test_fields_is_an_array_of_strings(app, name):
    fields = _props(app, name)[0]["fields"]
    assert fields["type"] == "array"
    assert fields["items"] == {"type": "string"}
    assert not {"anyOf", "oneOf", "default"} & set(fields)


@pytest.mark.parametrize(
    "domain",
    [[["is_company", "=", True]], '[["is_company", "=", true]]', None],
    ids=["list", "json-text", "omitted"],
)
def test_validation_still_takes_a_list_text_or_nothing(app, domain):
    model = app._tool_manager.get_tool("record_search").fn_metadata.arg_model
    args = {"model": "res.partner", "fields": "name, email"}
    if domain is not None:
        args["domain"] = domain
    parsed = model.model_validate(args)
    assert parsed.domain == domain
    assert parsed.fields == "name, email"
