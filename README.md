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

## Deployment

The MCP server supports two transports:

| Transport | Use case | Auth |
|-----------|----------|------|
| `stdio` (default) | Claude Desktop / Claude Code (local) | None needed (local process) |
| `streamable-http` | Claude.ai (web), multi-user | OAuth 2.1 (recommended) |

### Local (stdio)

Claude Desktop or Claude Code spawns the MCP server as a subprocess. No hosting, no OAuth.

### Remote (streamable-http)

Each Odoo instance runs as a separate MCP server container behind Caddy on a VPS.
OAuth 2.1 protects access — users authenticate via Zitadel (or any OIDC provider).

```
Claude.ai / Claude Code
        │
        │  OAuth 2.1 (Bearer token)
        │
        ▼ HTTPS
Hetzner VPS (mcp.example.com)
   Caddy :443
        │
        ├── /production/  →  mcp-production:8000  →  Odoo (odoo.sh)
        ├── /staging/     →  mcp-staging:8000     →  Odoo (staging)
        └── /local/       →  mcp-local:8000       →  Odoo (self-hosted)
                                    │
                                    │ Token introspection (RFC 7662)
                                    ▼
                              Zitadel (auth.example.com)
```

Odoo.sh cannot host the MCP server (managed platform, no custom processes or open ports).
A separate lightweight VPS (CX22, ~€4/m) is sufficient — it only proxies API calls to Odoo.

## Authentication

### How it works

1. Claude.ai sends a request → MCP server responds **401** with `WWW-Authenticate` header
2. Claude.ai opens a browser window → user logs in via Zitadel (PKCE flow)
3. Claude.ai receives a Bearer token → sends it with all subsequent requests
4. MCP server validates the token via Zitadel's introspection endpoint
5. If valid, the request proceeds to Odoo using the server-side API key

**Key security properties:**
- Client tokens authenticate to the MCP server only
- Odoo API key stays server-side (`ODOO_API_KEY` env var), never exposed to clients
- Token validation via Zitadel introspection (not local JWT parsing)
- OAuth is optional — omit `OAUTH_ISSUER_URL` for stdio/local use

### Admin setup (one-time)

1. **Deploy Zitadel** (self-hosted, free) or use Zitadel Cloud
2. **Create a project** in Zitadel
3. **Add an application** (type: Web, auth method: PKCE)
   - Redirect URI: `https://claude.ai/oauth/callback` (or your client's callback)
4. **Create a service user** for token introspection (client credentials)
5. **Set environment variables** on the MCP server (see Configuration below)

### User flow

Users just click "Connect" in Claude.ai → log in via Zitadel → done.
No API keys, no URLs to configure. The admin handles all setup.

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

### Odoo connection

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

### OAuth 2.1 (optional — for HTTP transport)

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

### Multi-instance deployment

For deploying multiple Odoo instances on a single VPS:

```bash
cd deploy
cp instances.example.yml instances.yml   # edit with your Odoo credentials
python generate.py                        # generates docker-compose + Caddyfile
cd generated
docker compose up -d --build
```

See [architecture.md](architecture.md) for the full architecture breakdown.

## License

[Mozilla Public License 2.0](LICENSE) — same as upstream.

## Credits

Built on top of [ivnvxd/mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) by Andrey Ivanov.
JSON/2 API: [Odoo 19.0 External API docs](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html).
