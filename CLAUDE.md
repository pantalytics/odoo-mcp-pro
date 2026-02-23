# CLAUDE.md — Project context for Claude Code

## What this project is

**odoo-mcp-pro** — an MCP server connecting AI assistants (Claude) to Odoo ERP via the
Odoo 19 JSON/2 API. Works on Claude.ai, Claude Code, and Claude Desktop. Runs locally
(stdio) or in the cloud (streamable-http + OAuth 2.1 via Zitadel).

Originally forked from [ivnvxd/mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) (MPL-2.0),
now a standalone project with JSON/2 client, OAuth 2.1, and cloud deployment.

## Current state

- JSON/2 client fully implemented (Odoo 19+, ready for Odoo 20)
- XML-RPC still supported for backwards compatibility (Odoo 14-18)
- OAuth 2.1 via Zitadel for cloud deployments
- Cloud deployment: Docker + Caddy reverse proxy, multi-instance
- Both stdio and streamable-http transports working
- 35 test files (incl. OAuth), 475+ unit tests, all mocked

## Architecture

Connection layer abstracted behind `OdooConnectionProtocol`. Factory in `server.py`:

```
ODOO_API_VERSION=json2   →  OdooJSON2Connection   (Odoo 19+)
ODOO_API_VERSION=xmlrpc  →  OdooConnection        (Odoo 14-18)
```

OAuth flow (cloud):
```
Claude.ai / Claude Code
    │  OAuth 2.1 (Bearer token)
    ▼
Caddy (TLS) → MCP container → Odoo (server-side API key)
    │
    └── Token introspection → Zitadel
```

See [architecture.md](architecture.md) for the full breakdown.

## Key files

| File | Role |
|------|------|
| `mcp_server_odoo/server.py` | Factory pattern, OAuth wiring, FastMCP setup |
| `mcp_server_odoo/connection_protocol.py` | Protocol class defining the connection interface |
| `mcp_server_odoo/odoo_json2_connection.py` | JSON/2 client using httpx |
| `mcp_server_odoo/odoo_connection.py` | XML-RPC client (Odoo 14-18) |
| `mcp_server_odoo/oauth.py` | `ZitadelTokenVerifier` — token validation via introspection |
| `mcp_server_odoo/config.py` | OdooConfig with `api_version` field |
| `mcp_server_odoo/tools.py` | 6 MCP tools with smart field selection |
| `mcp_server_odoo/resources.py` | 4 MCP resources (URI-based) |
| `mcp_server_odoo/access_control.py` | YOLO / standard / JSON/2 access control |

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
| `ZITADEL_CLIENT_ID` | Service user client ID (for introspection) |
| `ZITADEL_CLIENT_SECRET` | Service user client secret |
| `OAUTH_RESOURCE_SERVER_URL` | Public URL of this MCP server (for RFC 9728 metadata) |
| `OAUTH_EXPECTED_AUDIENCE` | Optional: Zitadel app/project ID for audience validation |

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

## Conventions

- Follow existing code style (ruff configured in pyproject.toml)
- Keep JSON/2 client in separate file — do not modify odoo_connection.py
- Both connection classes must satisfy OdooConnectionProtocol
- No new dependencies without discussion (httpx already available)

## Deployment (cloud)

Multi-instance: each Odoo instance = separate MCP container behind Caddy.

```
deploy/
├── instances.example.yml   # template — copy to instances.yml
├── generate.py             # generates docker-compose + Caddyfile
└── generated/              # output (.gitignored)
```

```bash
cd deploy
cp instances.example.yml instances.yml
python generate.py
cd generated && docker compose up -d --build
```

Each instance at `https://<domain>/<instance-name>/`.
Users connect via Claude.ai → OAuth login via Zitadel → done.
