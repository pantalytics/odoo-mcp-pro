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

---

> **"Show me all unpaid invoices over €5,000 from Q4"** — and Claude queries your Odoo instance directly.
> No copy-pasting, no CSV exports, no switching tabs.

## Choose your setup

| You want | Setup | Time | Guide |
|----------|-------|------|-------|
| Try it locally (Claude Code / Desktop) | **Local** | 5 min | [Quick start](#quick-start) below |
| Use Claude.ai (web) or share with your team | **Cloud** | 1-2 hrs | [SETUP.md](SETUP.md) |
| Odoo 14-18 (legacy) | **Local + XML-RPC** | 5 min | [XML-RPC setup](#odoo-14-18-xml-rpc) |

## Quick start

**You need:** Python 3.10+, an Odoo 19+ instance, and an [Odoo API key](SETUP.md#generating-an-odoo-api-key).

```bash
# Install
git clone https://github.com/rutgerhofste/odoo-mcp-pro.git
cd odoo-mcp-pro && uv venv && source .venv/bin/activate && uv pip install -e .

# Connect to Claude Code
claude mcp add -s user \
  -e ODOO_URL=https://your-odoo.com \
  -e ODOO_DB=your_database \
  -e ODOO_API_KEY=your_api_key \
  -e ODOO_API_VERSION=json2 \
  -e ODOO_YOLO=true \
  -- odoo python -m mcp_server_odoo
```

Ask Claude: *"List the 5 most recent sale orders"* — if it returns data, you're set.

<details>
<summary><b>Claude Desktop</b> — add to claude_desktop_config.json</summary>

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

</details>

<details>
<summary><b>Odoo 14-18 (XML-RPC)</b></summary>

For Odoo versions before 19, use XML-RPC (note: XML-RPC is removed in Odoo 20):

```bash
claude mcp add -s user \
  -e ODOO_URL=https://your-odoo.com \
  -e ODOO_DB=your_database \
  -e ODOO_USER=you@example.com \
  -e ODOO_API_KEY=your_api_key \
  -e ODOO_YOLO=true \
  -- odoo uvx mcp-server-odoo
```

</details>

## What can you do with it?

| Tool | What it does |
|------|-------------|
| `search_records` | Search any model with domain filters, sorting, pagination |
| `get_record` | Fetch a specific record by ID with smart field selection |
| `list_models` | Discover available Odoo models |
| `create_record` | Create a new record in any model |
| `update_record` | Update fields on an existing record |
| `delete_record` | Delete a record |

Plus **4 MCP resources** for URI-based access to records, search results, field definitions, and record counts.

**Example questions:**
- *"Find all contacts in Amsterdam with open quotations"*
- *"Create a lead for Acme Corp, expected revenue €50k"*
- *"Which sales orders from last month don't have a delivery yet?"*
- *"What fields does the sale.order model have?"*

## Cloud deployment

For **Claude.ai** (web) or multi-user setups, deploy on a VPS with OAuth 2.1. Users click "Connect" in Claude.ai, log in once via Zitadel, and they're in — no API keys, no technical setup needed.

```
Claude.ai → OAuth 2.1 → Caddy (TLS) → MCP Server → Odoo
                              ↕
                        Zitadel (IdP)
```

**[Full cloud setup guide](SETUP.md)** — covers VPS provisioning, Zitadel, OAuth, and optional Microsoft Entra ID federation.

## Security & privacy

**Does the MCP server see my data?** It's a proxy — data flows from Claude through MCP to Odoo and back. Nothing is stored.

**Is my Odoo API key exposed?** No. The API key stays server-side. Local: on your machine. Cloud: inside the Docker container. Users authenticate via OAuth tokens, never with the API key.

**Can Claude.ai users see each other's data?** Each user gets their own OAuth token. The Odoo API key determines what data is accessible — Odoo enforces row-level security (ACLs and record rules) server-side.

See [architecture.md](architecture.md) for the full security model.

## How it compares

| | odoo-mcp-pro | Other MCP servers |
|--|:---:|:---:|
| **Odoo 19 JSON/2 API** | Yes | XML-RPC or JSON-RPC |
| **Odoo 20 ready** | Yes | No (XML-RPC removed) |
| **OAuth 2.1 security** | Built-in (Zitadel) | None |
| **Claude.ai (web)** | Yes | Most: local only |
| **Multi-instance** | Yes (Caddy routing) | No |
| **No Odoo module needed** | Yes | Some require custom modules |
| **Test suite** | 35 files, 475+ tests | Varies |

## Development

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/               # 35 test files, all mocked
```

See [CLAUDE.md](CLAUDE.md) for architecture details and coding conventions.

## Contributing

Contributions are welcome! Fork the repo, create a feature branch, run `pytest tests/` and `ruff check .`, then open a PR.

## License

[Mozilla Public License 2.0](LICENSE)

## Built by Pantalytics

**odoo-mcp-pro** is built by [Pantalytics](https://pantalytics.com) — an Odoo implementation partner in Utrecht, Netherlands. Need help connecting Claude to your Odoo instance? [Get in touch](https://pantalytics.com).

## Acknowledgments

Originally forked from [mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) by Andrey Ivanov (MPL-2.0). Since expanded with JSON/2 client, OAuth 2.1, multi-instance cloud deployment, and comprehensive test suite.

---

<p align="center">
  If odoo-mcp-pro is useful to you, consider giving it a star — it helps others find the project.
</p>

<sub>Odoo is a registered trademark of <a href="https://www.odoo.com">Odoo S.A.</a> The MCP logo is used under the <a href="https://github.com/modelcontextprotocol/modelcontextprotocol">MIT License</a>. This project is not affiliated with or endorsed by Odoo S.A. or Anthropic.</sub>
