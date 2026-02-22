# CLAUDE.md — Project context for Claude Code

## What this project is

A fork of [ivnvxd/mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) (MPL-2.0).

Goal: add support for the **Odoo 19 JSON/2 API** alongside the existing XML-RPC client,
then decide whether to PR upstream or publish as a standalone project.

## Current state

- Cloned from upstream on 2026-02-22
- Working branch: `feature/json2-client`
- JSON/2 client implemented and integrated
- All 415 existing tests pass (0 regressions)
- Not yet tested against a live Odoo 19 instance

## Architecture

The connection layer is abstracted behind `OdooConnectionProtocol` (Protocol class).
`server.py` uses a factory pattern to pick the right implementation at startup:

```
ODOO_API_VERSION=xmlrpc  →  OdooConnection       (XML-RPC, Odoo 14-19)
ODOO_API_VERSION=json2   →  OdooJSON2Connection   (JSON/2, Odoo 19+ only)
```

See [architecture.md](architecture.md) for the full breakdown.

## Key files

| File | Role |
|------|------|
| `mcp_server_odoo/connection_protocol.py` | Protocol class defining the connection interface |
| `mcp_server_odoo/odoo_json2_connection.py` | **NEW** — JSON/2 client using httpx |
| `mcp_server_odoo/odoo_connection.py` | Existing XML-RPC client (unchanged) |
| `mcp_server_odoo/server.py` | Factory pattern: picks connection based on config |
| `mcp_server_odoo/config.py` | OdooConfig with `api_version` field |
| `mcp_server_odoo/tools.py` | MCP tool definitions (type hints updated) |
| `mcp_server_odoo/resources.py` | MCP resource definitions (type hints updated) |
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

| Env var | Values | Default |
|---------|--------|---------|
| `ODOO_API_VERSION` | `xmlrpc`, `json2` | `xmlrpc` |
| `ODOO_URL` | URL | required |
| `ODOO_DB` | database name | required for json2 |
| `ODOO_API_KEY` | API key | required for json2 |

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

## Next steps

1. Test against live Odoo 19 instance
2. Remote deployment: Hetzner VPS + Caddy + `streamable-http` transport for Claude.ai access
   - Odoo.sh can't host the MCP server (managed platform, no custom processes)
   - Need: Dockerfile, docker-compose.yml, Caddyfile
   - Target: production Odoo instance URL (set via env var)
3. Decide: PR to ivnvxd or publish as separate package
