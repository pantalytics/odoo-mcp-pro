# CLAUDE.md — Project context for Claude Code

## What this project is

A fork of [ivnvxd/mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) (MPL-2.0).

Goal: add support for the **Odoo 19 JSON/2 API** alongside the existing XML-RPC client,
then decide whether to PR upstream or publish as a standalone project.

## Current state

- Cloned from upstream on 2026-02-22
- Working branch: `feature/json2-client`
- JSON/2 client implemented and integrated
- OAuth 2.1 authentication for HTTP transport (via Zitadel)
- Deployed on Hetzner VPS behind Caddy

## Architecture

The connection layer is abstracted behind `OdooConnectionProtocol` (Protocol class).
`server.py` uses a factory pattern to pick the right implementation at startup:

```
ODOO_API_VERSION=xmlrpc  →  OdooConnection       (XML-RPC, Odoo 14-19)
ODOO_API_VERSION=json2   →  OdooJSON2Connection   (JSON/2, Odoo 19+ only)
```

### Authentication (HTTP transport)

```
Claude.ai / Claude Code
    │  OAuth 2.1 (Bearer token)
    ▼
Caddy (TLS) → MCP container → Odoo (via server-side API key)
    │
    └── Token introspection → Zitadel
```

- FastMCP handles OAuth middleware (401 responses, token validation, metadata endpoints)
- `ZitadelTokenVerifier` validates tokens via RFC 7662 introspection
- Odoo credentials (`ODOO_API_KEY`) are server-side env vars, never exposed to clients
- OAuth is optional — omit `OAUTH_ISSUER_URL` to run without auth (stdio/local)

See [architecture.md](architecture.md) for the full breakdown.

## Key files

| File | Role |
|------|------|
| `mcp_server_odoo/connection_protocol.py` | Protocol class defining the connection interface |
| `mcp_server_odoo/odoo_json2_connection.py` | JSON/2 client using httpx |
| `mcp_server_odoo/odoo_connection.py` | Existing XML-RPC client (unchanged) |
| `mcp_server_odoo/oauth.py` | `ZitadelTokenVerifier` — OAuth token validation via introspection |
| `mcp_server_odoo/server.py` | Factory pattern, OAuth wiring, FastMCP setup |
| `mcp_server_odoo/config.py` | OdooConfig with `api_version` field |
| `mcp_server_odoo/tools.py` | MCP tool definitions |
| `mcp_server_odoo/resources.py` | MCP resource definitions |
| `mcp_server_odoo/access_control.py` | JSON/2 mode delegates security to Odoo |

## JSON/2 API key points

- Endpoint: `POST /json/2/{model}/{method}`
- Auth: `Authorization: Bearer <api_key>` header
- Database: `X-Odoo-Database: <db>` header
- Body: flat JSON with named args, `ids` and `context` are top-level keys
- Create/write use `vals` (not `values`)
- Responses are raw JSON (no RPC envelope)
- Errors return proper HTTP status codes (401, 403, 404, 422, 500)
- No Odoo module required — Odoo 19 handles ACLs server-side

## Config

### Odoo connection

| Env var | Values | Default |
|---------|--------|---------|
| `ODOO_API_VERSION` | `xmlrpc`, `json2` | `xmlrpc` |
| `ODOO_URL` | URL | required |
| `ODOO_DB` | database name | required for json2 |
| `ODOO_API_KEY` | API key | required for json2 |
| `ODOO_MCP_TRANSPORT` | `stdio`, `streamable-http` | `stdio` |
| `ODOO_MCP_HOST` | bind address | `localhost` |
| `ODOO_MCP_PORT` | port | `8000` |
| `ODOO_YOLO` | `off`, `read`, `true` | `off` |

### OAuth 2.1 (optional, HTTP transport only)

| Env var | Description |
|---------|-------------|
| `OAUTH_ISSUER_URL` | Zitadel instance URL (enables OAuth when set) |
| `ZITADEL_INTROSPECTION_URL` | Token introspection endpoint |
| `ZITADEL_CLIENT_ID` | Service user client ID |
| `ZITADEL_CLIENT_SECRET` | Service user client secret |
| `OAUTH_RESOURCE_SERVER_URL` | Public URL of this MCP server (for metadata) |

## Development setup

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Testing

```bash
pytest tests/               # unit tests (mocked)
pytest tests/ -x -q         # quick run, stop on first failure
```

Integration test against live Odoo 19:
```bash
ODOO_URL=http://localhost:8069 ODOO_DB=test ODOO_API_KEY=... ODOO_API_VERSION=json2 \
  python -m mcp_server_odoo
```

## Conventions

- Follow existing code style (ruff configured in pyproject.toml)
- Keep JSON/2 client in separate file — do not modify odoo_connection.py
- Both connection classes must satisfy OdooConnectionProtocol
- No new dependencies (httpx is already available)

## Deployment (Hetzner VPS)

Multi-instance deployment: each Odoo instance runs as a separate MCP server container behind Caddy.
OAuth protects access — users authenticate via Zitadel (browser login).

```
deploy/
├── instances.example.yml   # template — copy to instances.yml
├── generate.py             # generates docker-compose + Caddyfile
└── generated/              # output (.gitignored)
    ├── docker-compose.yml
    └── Caddyfile
```

Quick start:
```bash
cd deploy
cp instances.example.yml instances.yml   # edit with your Odoo credentials + OAuth config
python generate.py                        # generates deployment files
cd generated
docker compose up -d --build              # start all instances
```

Each instance is accessible at `https://<domain>/<instance-name>/`.
Users connect via Claude.ai or Claude Code — OAuth login happens automatically.

### Admin setup (one-time)

1. Configure Zitadel project with MCP server application
2. Create service user for token introspection → get `client_id` + `client_secret`
3. Register Claude.ai as OAuth client (redirect URI from Anthropic)
4. Set env vars in Coolify / `instances.yml`
5. Deploy with `docker compose up -d --build`

### User flow

1. Add MCP server URL in Claude.ai → `https://mcp.example.com/production/mcp`
2. Claude.ai triggers OAuth flow → browser redirect to Zitadel login
3. User authenticates → token returned to Claude.ai
4. All subsequent MCP requests use Bearer token automatically

## Next steps

1. Test OAuth flow end-to-end with Claude.ai
2. Decide: PR to ivnvxd or publish as separate package
