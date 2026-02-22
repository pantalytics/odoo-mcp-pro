<p align="center">
  <a href="https://www.odoo.com"><img src="assets/odoo-logo.svg" alt="Odoo" height="60"/></a>
  &nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://modelcontextprotocol.io"><img src="assets/mcp-logo.svg" alt="Model Context Protocol" height="60"/></a>
</p>

<h1 align="center">odoo-mcp-pro</h1>

<p align="center">
  Connect Claude to your Odoo ERP — search, create, update, and manage records<br/>
  using natural language. Powered by the Odoo 19 JSON/2 API.
</p>

<p align="center">
  <a href="https://github.com/rutgerhofste/odoo-mcp-pro/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MPL%202.0-blue.svg" alt="License: MPL 2.0"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"/></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green.svg" alt="MCP Compatible"/></a>
  <a href="https://www.odoo.com/documentation/19.0/developer/reference/external_api.html"><img src="https://img.shields.io/badge/Odoo-19%2B%20JSON%2F2-714b67.svg" alt="Odoo 19+ JSON/2"/></a>
  <a href="https://oauth.net/2.1/"><img src="https://img.shields.io/badge/OAuth-2.1-orange.svg" alt="OAuth 2.1"/></a>
</p>

<p align="center">
  <a href="#quick-start--local">Quick Start</a> ·
  <a href="#cloud-deployment">Cloud Deploy</a> ·
  <a href="architecture.md">Architecture</a> ·
  <a href="#configuration">Config</a>
</p>

---

> **"Show me all unpaid invoices over €5,000 from Q4"** — and Claude queries your Odoo instance directly.
> No copy-pasting, no CSV exports, no switching tabs.

**odoo-mcp-pro** is an [MCP server](https://modelcontextprotocol.io) that gives Claude direct, secure
access to your Odoo ERP. It uses the modern **Odoo 19 JSON/2 API** and is the only Odoo MCP server
with built-in **OAuth 2.1 authentication** for secure multi-user cloud deployments.

## Why odoo-mcp-pro?

| | odoo-mcp-pro | Other MCP servers |
|--|:---:|:---:|
| **Odoo 19 JSON/2 API** | Yes | XML-RPC or JSON-RPC |
| **Odoo 20 ready** | Yes | No (XML-RPC removed in 20) |
| **OAuth 2.1 security** | Built-in (Zitadel) | None |
| **Claude.ai (web)** | Yes | Most: local only |
| **Claude Code & Desktop** | Yes | Yes |
| **Multi-instance** | Yes (Caddy routing) | No |
| **No Odoo module needed** | Yes | Some require custom modules |
| **Test suite** | 34 test files | Varies |

## What can you do with it?

Once connected, Claude can:

- **Search & filter** — "Find all contacts in Amsterdam with open quotations"
- **Create records** — "Create a lead for Acme Corp, expected revenue €50k"
- **Update data** — "Mark invoice INV-2024-0042 as paid"
- **Explore your data model** — "What fields does the sale.order model have?"
- **Cross-reference** — "Which sales orders from last month don't have a delivery yet?"

All through natural conversation. Claude decides which tools to call — you just ask.

## Architecture

```
Claude.ai / Claude Code / Claude Desktop
        │
        │  MCP protocol
        │  + OAuth 2.1 (cloud deployments)
        │
        ▼
  ┌─────────────────────┐
  │   odoo-mcp-pro      │
  │                     │
  │  FastMCP framework  │
  │  6 tools · 4 resources │
  │  Smart field selection │
  │  Access control     │
  └─────────┬───────────┘
            │
            │  JSON/2 API (Odoo 19+)
            │  or XML-RPC (Odoo 14-18)
            │
            ▼
      Odoo instance
```

For cloud deployments with OAuth 2.1:

```
Claude.ai ──────── OAuth 2.1 (Bearer) ──────────▶ VPS
                                                    │
                                              Caddy :443
                                                    │
                                    ┌───────────────┼───────────────┐
                                    ▼               ▼               ▼
                              /production/    /staging/        /local/
                                    │               │               │
                                    ▼               ▼               ▼
                              Odoo (prod)    Odoo (staging)   Odoo (dev)
                                    │
                              Token introspection
                                    ▼
                              Zitadel (IdP)
```

See [architecture.md](architecture.md) for the full technical breakdown.

## Tools

| Tool | Description |
|------|-------------|
| `search_records` | Search any model with domain filters, sorting, pagination |
| `get_record` | Fetch a specific record by ID with field selection |
| `list_models` | Discover available Odoo models |
| `create_record` | Create a new record in any model |
| `update_record` | Update fields on an existing record |
| `delete_record` | Delete a record |

Plus **4 MCP resources** for URI-based access to records, search results, field definitions, and record counts.

## Quick start — local

**Requirements:** Python 3.10+, an Odoo 19+ instance, an [Odoo API key](#generating-an-odoo-api-key)

### Claude Code (recommended)

```bash
git clone https://github.com/rutgerhofste/odoo-mcp-pro.git
cd odoo-mcp-pro
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

Done. Ask Claude: *"List the 5 most recent sale orders"* to verify it works.

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "python",
      "args": ["-m", "mcp_server_odoo"],
      "cwd": "/path/to/odoo-mcp-pro",
      "env": {
        "ODOO_URL": "https://your-odoo.com",
        "ODOO_DB": "your_database",
        "ODOO_API_KEY": "your_api_key",
        "ODOO_API_VERSION": "json2",
        "ODOO_YOLO": "true"
      }
    }
  }
}
```

### Odoo 14-18 (XML-RPC)

```bash
claude mcp add -s user \
  -e "ODOO_URL=https://your-odoo.com" \
  -e "ODOO_DB=your_database" \
  -e "ODOO_USER=you@example.com" \
  -e "ODOO_API_KEY=your_api_key" \
  -e "ODOO_YOLO=true" \
  -- odoo uvx mcp-server-odoo
```

## Cloud deployment

For **Claude.ai** (web) or multi-user setups, deploy the MCP server on a VPS with OAuth 2.1.
Users just click "Connect" in Claude.ai, log in once, and they're in — no API keys, no technical setup.

### How it works

1. Claude.ai connects to your MCP server URL
2. Server responds 401 → Claude.ai opens Zitadel login in browser
3. User authenticates → Bearer token sent with all requests
4. MCP server validates token via Zitadel introspection
5. Valid? → request proceeds to Odoo (using server-side API key)

**Security properties:**
- Odoo API key stays server-side — never exposed to users
- Tokens validated via Zitadel introspection (RFC 7662)
- Per-user authentication — know who's accessing what
- OAuth is optional — omit config for local/stdio use

### Deploy in 3 steps

```bash
cd deploy
cp instances.example.yml instances.yml   # 1. edit with your credentials
python generate.py                        # 2. generates docker-compose + Caddyfile
cd generated && docker compose up -d --build  # 3. done
```

Your MCP server is now live at `https://mcp.yourdomain.com/production/mcp`.

Users add this URL in Claude.ai → log in via Zitadel → start querying Odoo.

### Admin setup (one-time)

1. **Deploy Zitadel** — self-hosted (free) or [Zitadel Cloud](https://zitadel.com)
2. **Create a project + application** — type: Web, auth method: PKCE
   - Redirect URI: `https://claude.ai/oauth/callback`
3. **Create a service user** — for token introspection (client credentials)
4. **Configure `instances.yml`** — Odoo credentials + OAuth config
5. **Deploy** — `docker compose up -d --build`

See the [instances.example.yml](deploy/instances.example.yml) for a complete template.

## Configuration

### Odoo connection

| Variable | Default | Description |
|----------|---------|-------------|
| `ODOO_URL` | *required* | Your Odoo instance URL |
| `ODOO_DB` | *required* | Database name |
| `ODOO_API_KEY` | *required* | Odoo API key |
| `ODOO_USER` | — | Username (XML-RPC only) |
| `ODOO_API_VERSION` | `xmlrpc` | `xmlrpc` or `json2` |
| `ODOO_YOLO` | `false` | `true` = all models, `read` = read-only access |
| `ODOO_MCP_DEFAULT_LIMIT` | `10` | Default search result limit |
| `ODOO_MCP_MAX_LIMIT` | `100` | Maximum search result limit |

### OAuth 2.1 (optional — for cloud deployments)

| Variable | Description |
|----------|-------------|
| `OAUTH_ISSUER_URL` | Zitadel instance URL (enables OAuth when set) |
| `ZITADEL_INTROSPECTION_URL` | Token introspection endpoint |
| `ZITADEL_CLIENT_ID` | Service user client ID |
| `ZITADEL_CLIENT_SECRET` | Service user client secret |

### Transport

| Variable | Default | Description |
|----------|---------|-------------|
| `ODOO_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `ODOO_MCP_HOST` | `localhost` | Bind host (HTTP transport) |
| `ODOO_MCP_PORT` | `8000` | Bind port (HTTP transport) |

## Generating an Odoo API key

Odoo → your profile → **Account Security** → **API Keys** → **New**

Or via the Odoo shell:

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
pytest tests/               # 34 test files, all mocked
pytest tests/ -x -q         # quick run, stop on first failure
```

## Contributing

Contributions are welcome! Please:

1. Fork the repo
2. Create a feature branch
3. Run `pytest tests/` and `ruff check .` before submitting
4. Open a PR with a clear description

## License

[Mozilla Public License 2.0](LICENSE)

## Built by Pantalytics

**odoo-mcp-pro** is built and maintained by [Pantalytics](https://pantalytics.com) — an Odoo implementation partner based in Utrecht, Netherlands. We help businesses connect their sales, support, and operations on a single platform, using AI to make ERP work smarter.

Need help connecting Claude to your Odoo instance? [Get in touch](https://pantalytics.com).

## Acknowledgments

Originally forked from [mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) by Andrey Ivanov (MPL-2.0). Since then, odoo-mcp-pro has been significantly expanded with the Odoo 19 JSON/2 client, OAuth 2.1 authentication, multi-instance cloud deployment, and a comprehensive test suite.

JSON/2 API reference: [Odoo 19 External API docs](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html).

---

<p align="center">
  If odoo-mcp-pro is useful to you, consider giving it a ⭐ — it helps others find the project.
</p>

<sub>Odoo is a registered trademark of [Odoo S.A.](https://www.odoo.com) The MCP logo is used under the [MIT License](https://github.com/modelcontextprotocol/modelcontextprotocol). This project is not affiliated with or endorsed by Odoo S.A. or Anthropic.</sub>
