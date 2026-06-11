# CLAUDE.md -- Instructions for Claude Code

## What this project is

**odoo-mcp-pro** -- an open source MCP server connecting AI to Odoo ERP.
Supports Odoo 14-19+, stdio and streamable-http transport.

This is the **public** package. The admin panel, billing, and deploy infrastructure
live in the private repo: `pantalytics/odoo-mcp-pro-admin`.

## Design principles

1. **Odoo + AI, samen sterker** -- don't replace Odoo, make it more accessible via AI
2. **Use the interface that fits** -- Odoo UI for complex config, Claude for quick queries and data entry
3. **Odoo is the boss** -- all data, permissions, and business logic live in Odoo; MCP server is a stateless proxy
4. **No fallbacks** -- explicit configuration or clear errors, never guess
5. **Open core** -- public package works standalone, private package adds SaaS features

## Key architecture facts

- Connection factory: `OdooJSON2Connection` (Odoo 19+) / `OdooConnection` (Odoo 14-18, XML-RPC)
- `odoo_knowledge.py` provides Odoo domain knowledge via MCP server instructions
- Single-tenant only: one Odoo instance from env vars (stdio or HTTP). The hosted
  multi-tenant deployment lives in the private admin package.

## Open core boundary

The public repo works standalone for single-tenant use (stdio or HTTP + API key).
The SaaS features (multi-tenant, OAuth, admin UI, billing) live in the private
`odoo-mcp-pro-admin` package, which has **its own entry point**
(`python -m mcp_server_odoo_admin`) and imports this package as a normal,
**tag-pinned** dependency. There is no file overlay anymore.

**Implications for changes in this repo:**
- Public main never reaches production directly: the admin repo pins
  `mcp-server-odoo @ git+...@vX.Y.Z`. Ship changes by tagging a release and
  bumping the pin in the admin repo.
- Admin imports from public: `config.OdooConfig`, `server.SERVER_VERSION`,
  `server.create_fastmcp_app`, `odoo_connection.OdooConnection`,
  `odoo_json2_connection.OdooJSON2Connection`, `connection_protocol`,
  `access_control.AccessController`, `error_handling.ValidationError`,
  `exceptions.OdooConnectionError`, `performance.PerformanceManager`,
  `detection.detect_odoo`, `version_detect.detect_api_version`,
  `xmlrpc_transport.{transport_for_url, DEFAULT_XMLRPC_TIMEOUT}`.
  Breaking any of these signatures breaks admin.
- `usage.py` is a no-op `track_event` stub so the public package works
  standalone; the real tracker lives in the admin package.

**Admin extension contract** (admin subclasses/imports these — rename only in
coordination with the admin repo):
- `server.create_fastmcp_app(*, auth=None, token_verifier=None)` — single source
  of truth for FastMCP construction; admin's multi-tenant entry point uses it
- `tools.OdooToolHandler._get_user_context` / `._track_usage` — hook methods the
  admin package overrides for per-user connection resolution and usage tracking
- `tools._current_sub` — contextvar carrying the authenticated subject
- `resources.OdooResourceHandler._get_user_context` — same hook for resources

## JSON/2 API key points

- Endpoint: `POST /json/2/{model}/{method}`
- Auth: `Authorization: Bearer <api_key>` header
- Database: `X-Odoo-Database: <db>` header
- Body: flat JSON with named args, `ids` and `context` are top-level keys
- Create/write use `vals` (not `values`)
- Responses are raw JSON (no RPC envelope)
- Errors return proper HTTP status codes (401, 403, 404, 422, 500)

## Development

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -x -q         # unit tests (mocked), stop on first failure
```

## Conventions

- Follow existing code style (ruff configured in pyproject.toml)
- Keep JSON/2 and XML-RPC clients in separate files -- do not merge them
- Both connection classes must satisfy `OdooConnectionProtocol`
- Shared exceptions live in `exceptions.py`
- No new dependencies without discussion (httpx already available)
- No hardcoded fallbacks -- explicit config or clear errors
- Self-hosted Odoo requires explicit database name (no auto-detection)
- Odoo.sh determines database by hostname (no database name needed)
- Max 500 lines per Python file (src and tests) — enforced by scripts/check_max_lines.py in CI

### Tests: open core split

This repo's test suite tests **only what ships in the OSS package**. SaaS-only
logic (`UsageTracker`, billing, teams, multi-tenancy) is tested in the private
`odoo-mcp-pro-admin` repo. Strict physical separation — no skip markers, no
conditional imports. Same pattern as Sentry/getsentry and PostHog `ee/`.

When changing or removing OSS behavior here, prune or update the test in the
**same PR**. When moving logic out to admin, the test moves along. CI must
never reference symbols that only exist in the admin overlay (caught at
collection time).

## Key files

| File | Role |
|------|------|
| `server.py` | Factory pattern, FastMCP setup, stdio/HTTP runners |
| `tools/` | MCP tools as mixins on `OdooToolHandler` (crud, query, bulk, binary, messaging, introspection) |
| `resources/` | MCP resources (URI-based): handler, retrieval, formatting |
| `schemas.py` | Pydantic result models |
| `odoo_json2_connection.py` | JSON/2 client (httpx, Odoo 19+); ORM mixin in `odoo_json2_orm.py` |
| `odoo_connection/` | XML-RPC client (stdlib, Odoo 14-18): core, auth, orm |
| `connection_protocol.py` | Protocol class for connection interface |
| `performance_cache.py` | Cache primitives backing `performance.py` |
| `detection_probes.py` | HTTP probes backing `detection.py` |
| `error_handler.py` | Error-handler internals backing `error_handling.py` |
| `odoo_knowledge.py` | Odoo domain knowledge (server instructions) |
| `config.py` | OdooConfig dataclass |
| `usage.py` | Usage tracking stub (full version in admin package) |
| `access_control.py` | Odoo ACL checks via check_access_rights |
