<p align="center">
  <a href="https://www.odoo.com"><img src="assets/odoo-logo.svg" alt="Odoo" height="60"/></a>
  &nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://modelcontextprotocol.io"><img src="assets/mcp-logo.svg" alt="Model Context Protocol" height="60"/></a>
</p>

<h1 align="center">odoo-mcp-pro</h1>

<p align="center">
  AI connector for Odoo ERP -- talk to your business data using natural language.<br/>
  Search, create, update, and manage records -- just ask.
</p>

<p align="center">
  <a href="https://github.com/pantalytics/odoo-mcp-pro/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Elastic%202.0-blue.svg" alt="License: Elastic 2.0"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"/></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green.svg" alt="MCP Compatible"/></a>
  <a href="https://www.odoo.com/documentation/19.0/developer/reference/external_api.html"><img src="https://img.shields.io/badge/Odoo-14--19+-714b67.svg" alt="Odoo 14-19+"/></a>
  <a href="https://oauth.net/2.1/"><img src="https://img.shields.io/badge/OAuth-2.1-orange.svg" alt="OAuth 2.1"/></a>
</p>

> **Skip the setup.** The hosted version is up and running in 5 minutes -- with automatic updates, managed OAuth, and encrypted API keys. **[Start at pantalytics.com →](https://pantalytics.com/en/apps/odoo-mcp-server)**

<p align="center">
  <img src="docs/ai-grid@2x.png" alt="Odoo connected to Claude, OpenAI, Gemini, Copilot, Mistral via MCP" width="500"/>
</p>

---

## The idea

Odoo is powerful. AI is powerful. Together they're better.

odoo-mcp-pro is an AI connector for Odoo that uses MCP (Model Context Protocol) -- an open standard that lets AI assistants talk to external systems. It connects your AI assistant to your Odoo ERP, so you can use natural language as an interface to your business data. Not to replace Odoo's UI -- but to give you a second interface that's faster for many tasks. Works with Claude, ChatGPT, Cursor, Windsurf, and any other MCP-compatible AI tool.

> **"Show me all unpaid invoices over 5,000 EUR from Q4"** -- your AI queries your Odoo instance directly and returns the results.

<p align="center">
  <img src="docs/demo.gif" alt="Demo of odoo-mcp-pro" width="800"/>
</p>

**Use the interface that fits the task.** Complex configuration? Use the Odoo UI. Quick data lookup, bulk questions, or creating records on the fly? Just ask your AI.

## What you can do

- *"Find all contacts in Amsterdam with open quotations"*
- *"Create a lead for Acme Corp, expected revenue 50k EUR"*
- *"Which sales orders from last month don't have a delivery yet?"*
- *"What fields does the sale.order model have?"*
- *"Update the expected closing date on opportunity #42 to next Friday"*

Works with any Odoo model -- sales, invoices, contacts, inventory, CRM, HR, you name it.

> **Want to try it now?** Skip the setup -- the hosted version is connected to your Odoo in under 5 minutes. **[Start at pantalytics.com →](https://pantalytics.com/en/apps/odoo-mcp-server)**

## Get started

### Hosted (recommended)

**The fastest and safest way to get started.** We run the server for you -- sign up, paste your Odoo URL + API key, done.

- **Up and running in 5 minutes.** No install, no Docker, no infrastructure. Works with Claude, ChatGPT, Cursor, Windsurf, and any MCP-compatible AI tool.
- **Always up to date.** Security patches, Odoo-version compatibility, and new MCP features ship automatically -- no upgrade work on your side.
- **Safer by default.** Encrypted API keys (AES-128), managed OAuth 2.1, continuous monitoring, European infrastructure. We handle the boring-but-critical stuff so you don't have to.

1. **[Sign up at pantalytics.com](https://pantalytics.com/en/apps/odoo-mcp-server)**
2. Log in and enter your Odoo URL + API key ([how to generate one](SETUP.md#generating-an-odoo-api-key))
3. Add the MCP server to your AI tool (Claude, ChatGPT, Cursor, etc.)
4. Start asking questions

Your data stays in Odoo -- the server is a stateless proxy. API keys are encrypted at rest.

**[Start at pantalytics.com →](https://pantalytics.com/en/apps/odoo-mcp-server)**

### Self-hosted

Run on your own machine. Single-tenant only: one Odoo instance, configured via env vars. Authentication is your Odoo API key -- no OAuth, no user management, no admin UI.

```bash
# Install
pip install "mcp-server-odoo @ git+https://github.com/pantalytics/odoo-mcp-pro.git"

# Run (stdio mode, for Claude Desktop / Claude Code)
ODOO_URL=https://mycompany.odoo.com ODOO_API_KEY=your_key python -m mcp_server_odoo

# Run (HTTP mode, for remote access from one trusted network)
ODOO_URL=https://mycompany.odoo.com ODOO_API_KEY=your_key \
  python -m mcp_server_odoo --transport streamable-http --host 0.0.0.0 --port 8000
```

HTTP mode has no built-in authentication -- expose it behind a reverse proxy or restrict at the network level.

Or add to your Claude Desktop `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "odoo": {
      "command": "python",
      "args": ["-m", "mcp_server_odoo"],
      "env": {
        "ODOO_URL": "https://mycompany.odoo.com",
        "ODOO_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

**Need multi-tenant, per-user connections, OAuth, or an admin UI?** That's SaaS territory. The [hosted version](https://pantalytics.com/en/apps/odoo-mcp-server) is faster to set up, auto-updates, and handles security patches for you -- and costs less than the time you'd spend self-hosting. These features live in a separate commercial package that builds on top of this open-core server.

## How it works

odoo-mcp-pro is an Odoo connector that implements [MCP (Model Context Protocol)](https://modelcontextprotocol.io) -- an open standard that lets AI assistants call external tools. It exposes the following tools that your AI can call based on your questions:

| Tool | What it does |
|------|-------------|
| `search_records` | Search any model with domain filters, sorting, pagination |
| `get_record` | Fetch a specific record by ID with smart field selection |
| `list_models` | Discover available Odoo models |
| `list_resource_templates` | Discover available resource URI templates |
| `create_record` / `create_records` | Create one or multiple records |
| `update_record` / `update_records` | Update one or multiple records |
| `delete_record` / `delete_records` | Delete one or multiple records |
| `import_records` | Idempotent upsert via external IDs (same as Odoo CSV import) |
| `server_info` | Server version, connection status |

**Supports Odoo 14-19+** -- uses the JSON/2 API for Odoo 19+ and XML-RPC for older versions. The right protocol is selected automatically.

## Security

- **Odoo is the boss.** All data, permissions, and business logic live in Odoo. Each user's API key determines what they can see and do -- ACLs and record rules apply as normal.
- **Stateless proxy.** The MCP server doesn't store or cache your business data.
- **API keys encrypted at rest** using AES-128 (Fernet). Never exposed to the AI, the browser, or logs.
- **You stay in control.** Revoke your API key in Odoo at any time to instantly cut off access.

**Prefer managed security?** The [hosted version](https://pantalytics.com/en/apps/odoo-mcp-server) handles patches, OAuth token rotation, and incident monitoring -- your data still stays in Odoo, we just run the proxy for you.

## Development

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -q
ruff check . && ruff format .
```

See [architecture.md](architecture.md) for technical details and [CLAUDE.md](CLAUDE.md) for coding conventions.

## Contributing

Contributions are welcome. Fork the repo, create a feature branch, run `pytest tests/` and `ruff check .`, then open a PR.

## FAQ

**Which Odoo versions are supported?**
Odoo 14-19+. The server auto-detects whether to use JSON/2 (Odoo 19+) or XML-RPC (14-18). No configuration needed.

**Does it work on my phone?**
Yes -- the hosted version works on Claude mobile (iOS/Android), Claude.ai in any browser, Claude Desktop, Claude Code, and ChatGPT. Local installs (STDIO) only work on the machine where they're installed.

**Is my data safe?**
Your data stays in Odoo. The MCP server is a stateless proxy -- it doesn't store or cache business data. API keys are encrypted at rest with AES-128 (Fernet). Each user's Odoo permissions apply: you can only see and do what your Odoo role allows.

**I get "Access denied" on all models**
This usually means your Odoo API key doesn't have the right permissions. Try:
1. **Regenerate your API key** in Odoo (Settings > Users > API Keys) and update it wherever you configured it (hosted: `/admin/setup`; self-hosted: your `ODOO_API_KEY` env var)
2. Make sure your Odoo user has at least read access to the models you want to query
3. If you're on Odoo.sh, verify your subscription plan supports the JSON/2 API

**I get "Authentication required" or "invalid_token"** (hosted version)
This means the OAuth connection between your AI tool and the MCP server failed. Try disconnecting and reconnecting the MCP server in your AI tool's settings. Self-hosted installs don't use OAuth -- they authenticate with your Odoo API key directly.

**Do I need to set ODOO_DB?**
Only if you self-host Odoo with multiple databases. Odoo.sh and Odoo Online don't need it -- the hostname determines the database.

**Should I use hosted or self-hosted?**
Hosted, unless you have a strong reason not to. Hosted is running in 5 minutes, auto-updates with every Odoo release, and has managed OAuth + encrypted API keys. Self-hosting is single-tenant only (one Odoo instance, API key auth, no admin UI), and you're on the hook for patches and security. **[Start with hosted at pantalytics.com →](https://pantalytics.com/en/apps/odoo-mcp-server)**

## License

[Elastic License 2.0](LICENSE). In plain terms:

- **You may** use, copy, modify, and distribute this software -- including for your own commercial purposes (running it inside your own company, your own Odoo instance, your own projects).
- **You may not** offer it to third parties as a hosted or managed service that provides them with access to a substantial set of its features -- that's what [Pantalytics](https://pantalytics.com) does, and what the license protects.
- **You may not** remove license notices or circumvent license key functionality.

Source-available, not OSI-open-source. See the full text in [LICENSE](LICENSE).

## For Odoo Implementation Partners

Want to offer AI-powered Odoo to your clients? Running your own hosted version of odoo-mcp-pro for clients is not permitted under the Elastic License 2.0. Instead, we run a Partner Program with a referral commission: you recommend odoo-mcp-pro to your end users, they sign up through your referral link, and you earn a recurring fee. No hosting or maintenance on your side.

Interested? Contact [rutger@pantalytics.com](mailto:rutger@pantalytics.com) for details.

## Built by Pantalytics

**odoo-mcp-pro** is built and maintained by [Pantalytics](https://pantalytics.com), an Odoo implementation partner based in Utrecht, Netherlands.

Originally forked from [mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) by Andrey Ivanov (originally MPL-2.0).

---

<sub>Odoo is a registered trademark of <a href="https://www.odoo.com">Odoo S.A.</a> The MCP logo is used under the <a href="https://github.com/modelcontextprotocol/modelcontextprotocol">MIT License</a>. This project is not affiliated with or endorsed by Odoo S.A. or Anthropic.</sub>
