# Setup Guide -- odoo-mcp-pro

This guide covers the open-core package: single-tenant, API-key-based, self-hosted use. The **multi-tenant, OAuth, admin UI version is hosted**, and not self-deployable from this repo -- see [pantalytics.com](https://pantalytics.com/en/apps/odoo-mcp-server).

## Choose your path

| You want | Setup | Time |
|----------|-------|------|
| Use it now, zero install, works from any AI tool | [Hosted (recommended)](#hosted-recommended) | 2 min |
| Run locally with Claude Code / Desktop | [Local setup](#local-setup) | 5 min |
| Run as an HTTP service on one trusted network | [HTTP mode](#http-mode-single-tenant) | 10 min |

---

## Hosted (recommended)

**The fastest and safest way to run odoo-mcp-pro.** We run the server for you -- no install, no Docker, no infrastructure to babysit.

- **Up and running in 5 minutes.** Works on your phone, laptop, and any browser. Supports Claude (mobile, desktop, web, Code), ChatGPT, Cursor, Windsurf, and any MCP-compatible tool.
- **Always up to date.** Security patches, Odoo-version compatibility, and new MCP features ship automatically.
- **Safer by default.** Encrypted API keys (AES-128), managed OAuth 2.1, continuous monitoring, European infrastructure.

1. Sign up at **[pantalytics.com/en/apps/odoo-mcp-server](https://pantalytics.com/en/apps/odoo-mcp-server)**
2. Log in and enter your **Odoo URL** + **API key** ([how to generate one](#generating-an-odoo-api-key))
3. Connect your AI tool to `https://mcp.pantalytics.com/mcp/`
4. Start asking questions

Supports Odoo 14-20.

---

## Local setup

Run the MCP server on your own machine, used from Claude Code or Claude Desktop via stdio. One Odoo instance, configured via env vars.

### Prerequisites

- Python 3.10+
- Odoo 14+ instance with an [API key](#generating-an-odoo-api-key)

### Install

```bash
git clone https://github.com/pantalytics/odoo-mcp-pro.git
cd odoo-mcp-pro
uv venv --python 3.10 && source .venv/bin/activate
uv pip install -e .
```

Or install directly without cloning:

```bash
pip install "mcp-server-odoo @ git+https://github.com/pantalytics/odoo-mcp-pro.git"
```

### Claude Code

```bash
claude mcp add -s user \
  -e ODOO_URL=https://your-odoo.com \
  -e ODOO_API_KEY=your_api_key \
  -- odoo python -m mcp_server_odoo
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "python",
      "args": ["-m", "mcp_server_odoo"],
      "env": {
        "ODOO_URL": "https://your-odoo.com",
        "ODOO_API_KEY": "your_api_key"
      }
    }
  }
}
```

Add `"cwd": "/path/to/odoo-mcp-pro"` if you installed from a clone rather than pip.

### Verify it works

Ask your AI: *"List the models available in Odoo"* or *"Search for contacts in res.partner"*.

---

## HTTP mode (single-tenant)

Run as a long-running HTTP service for remote access from one trusted network.

```bash
ODOO_URL=https://your-odoo.com ODOO_API_KEY=your_api_key \
  python -m mcp_server_odoo --transport streamable-http --host 0.0.0.0 --port 8000
```

**HTTP mode has no built-in authentication.** The whole server uses one Odoo API key -- anyone who reaches the endpoint gets the same access. Restrict access at the network level (firewall, VPN, reverse proxy with auth), or don't expose it publicly.

**Want per-user OAuth, a web admin UI, encrypted API-key storage, and multi-tenant support?** Those features live in the closed-source admin package that powers the [hosted version](https://pantalytics.com/en/apps/odoo-mcp-server) -- you can't deploy them yourself from this repo. **[Sign up at pantalytics.com →](https://pantalytics.com/en/apps/odoo-mcp-server)**

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ODOO_URL` | Yes | -- | Odoo server URL (e.g., `https://mycompany.odoo.com`) |
| `ODOO_API_KEY` | Yes* | -- | Odoo API key (preferred) |
| `ODOO_USER` | -- | -- | Odoo username (fallback if no API key) |
| `ODOO_PASSWORD` | -- | -- | Odoo password (required with username) |
| `ODOO_DB` | No | -- | Database name (self-hosted Odoo with multiple databases only) |
| `ODOO_MCP_TRANSPORT` | No | `stdio` | `stdio` or `streamable-http` |
| `ODOO_MCP_HOST` | No | `localhost` | Bind address for HTTP mode |
| `ODOO_MCP_PORT` | No | `8000` | Port for HTTP mode |
| `ODOO_MCP_LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `ODOO_MCP_DEFAULT_LIMIT` | No | `10` | Default search result limit |
| `ODOO_MCP_MAX_LIMIT` | No | `100` | Maximum search result limit |
| `ODOO_MCP_MAX_SMART_FIELDS` | No | `15` | Max fields in smart field selection |
| `ODOO_MCP_ALLOW_PRIVATE_FETCH` | No | `false` | Let `set_binary_field` fetch from private/LAN addresses. Off by default (SSRF guard); only for self-hosting with a file server on your own network |

*Either `ODOO_API_KEY` or `ODOO_USER` + `ODOO_PASSWORD`. API key is strongly preferred.

---

## Generating an Odoo API key

1. Log in to your Odoo instance
2. Click your avatar (top right) > **My Profile**
3. Scroll to **Account Security**
4. Click **New API Key**
5. Description: `MCP Server`
6. Click **Generate Key** -- copy immediately (shown only once)

**Tip:** create a dedicated Odoo user (e.g., `mcp@yourcompany.com`) with only the permissions the MCP server needs, rather than using your admin account.

### Do I need ODOO_DB?

- **Odoo.sh / Odoo Online**: No -- the hostname determines the database
- **Self-hosted with one database**: No -- auto-detected
- **Self-hosted with multiple databases**: Yes -- set `ODOO_DB` to the database name

---

## Troubleshooting

### Access denied on all models

Your Odoo API key doesn't have the right permissions. Try:
1. Regenerate the API key in Odoo (Settings > Users > API Keys) and update `ODOO_API_KEY`
2. Make sure the Odoo user has at least read access on the models you want to query
3. On Odoo.sh, verify your subscription plan supports the JSON/2 API (Odoo 19+) or XML-RPC (14-18)

### Connection fails / "database not found"

- Check `ODOO_URL` is the exact URL (https, no trailing slash)
- For self-hosted Odoo with multiple databases, set `ODOO_DB` explicitly
- For Odoo.sh and Odoo Online, don't set `ODOO_DB` -- the hostname determines it

### "No such model" / tool calls return empty

Make sure the Odoo user has the module installed AND read access to that model. An admin can verify via *Settings > Technical > Security > Access Rights*.

### Version detection fails for Odoo.sh

Odoo.sh SaaS versions use strings like `saas~19`. This is handled automatically since v1.0.1 -- upgrade to the latest version.

### I need multi-user auth / OAuth / an admin UI

That's not part of this open-core package. The [hosted version](https://pantalytics.com/en/apps/odoo-mcp-server) includes all of that, runs in 5 minutes, and auto-updates. **[Start at pantalytics.com →](https://pantalytics.com/en/apps/odoo-mcp-server)**

---

## Need help?

Open an issue on [GitHub](https://github.com/pantalytics/odoo-mcp-pro/issues).

Built by [Pantalytics](https://pantalytics.com) -- Odoo implementation partner in Utrecht, Netherlands.
