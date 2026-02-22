# mcp-server-odoo (JSON/2 fork)

> **Experimental fork** of [ivnvxd/mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo)
> adding support for the Odoo 19 JSON/2 API.

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that lets AI assistants
(Claude, etc.) interact directly with Odoo ERP — search records, create leads, look up contacts,
and more — without leaving your AI tool.

## Why this fork?

Odoo 19 introduced a new external API called **JSON/2** (`/json/2/...`). The original
`mcp-server-odoo` uses XML-RPC, which is [being removed in Odoo 20](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html).
This fork adds a JSON/2 client while keeping the existing XML-RPC client for older Odoo versions.

| | Upstream | This fork |
|--|----------|-----------|
| Protocol | XML-RPC | XML-RPC (14-18) + JSON/2 (19+) |
| Odoo 19 | Works | Works (native API) |
| Odoo 20 ready | No | Yes |
| Auth | XML-RPC args | Bearer token (JSON/2) |

## Status

**Experimental.** JSON/2 client is under active development. Not yet published to PyPI.
The existing XML-RPC path is unchanged from upstream and works today.

## Tools

| Tool | Description |
|------|-------------|
| `search_records` | Search any model with filters, sorting, pagination |
| `get_record` | Fetch a specific record by ID |
| `list_models` | List available Odoo models |
| `create_record` | Create a new record |
| `update_record` | Update an existing record |
| `delete_record` | Delete a record |

## Quick start (XML-RPC, works with Odoo 14-19)

```bash
claude mcp add -s user \
  -e "ODOO_URL=https://your-odoo.com" \
  -e "ODOO_DB=your_database" \
  -e "ODOO_USER=you@example.com" \
  -e "ODOO_API_KEY=your_api_key" \
  -e "ODOO_YOLO=true" \
  -- odoo uvx mcp-server-odoo
```

## Quick start (JSON/2, Odoo 19+)

```bash
git clone https://github.com/your-username/mcp-server-odoo.git
cd mcp-server-odoo
uv venv && source .venv/bin/activate
uv pip install -e .
```

```bash
claude mcp add -s user \
  -e "ODOO_URL=https://your-odoo.com" \
  -e "ODOO_DB=your_database" \
  -e "ODOO_API_KEY=your_api_key" \
  -e "ODOO_API_VERSION=json2" \
  -e "ODOO_YOLO=true" \
  -- odoo python -m mcp_server_odoo
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ODOO_URL` | required | Your Odoo instance URL |
| `ODOO_DB` | required | Database name |
| `ODOO_API_KEY` | required | Odoo API key |
| `ODOO_USER` | — | Username (XML-RPC only) |
| `ODOO_API_VERSION` | `xmlrpc` | `xmlrpc` or `json2` |
| `ODOO_YOLO` | `false` | `true` = all models, `read` = read-only |
| `ODOO_MCP_DEFAULT_LIMIT` | `10` | Default result limit |
| `ODOO_MCP_MAX_LIMIT` | `100` | Maximum result limit |

## Generating an Odoo API key

Odoo → your profile → **Account Security** → **API Keys** → **New**

Or via the Odoo shell if OAuth is in the way:

```bash
odoo shell -d your_database <<'EOF'
key = env['res.users.apikeys'].with_user(5)._generate('rpc', 'MCP', None)
env.cr.commit()
print('API_KEY=' + key)
EOF
```

## Development

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/
```

See [architecture.md](architecture.md) for a full breakdown of the codebase and the JSON/2
implementation plan.

## License

[Mozilla Public License 2.0](LICENSE) — same as upstream.

## Credits

Built on top of [ivnvxd/mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) by Andrey Ivanov.
JSON/2 API: [Odoo 19.0 External API docs](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html).
