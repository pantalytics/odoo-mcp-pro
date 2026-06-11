# CLAUDE.md -- Instructions for Claude Code

## What this project is

**odoo-mcp-pro** -- an open source MCP server connecting AI to Odoo ERP.
Supports Odoo 14-19+, stdio and streamable-http transport, OAuth 2.1.

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
- ConnectionRegistry caches connections per user (30 min TTL, multi-tenant only)
- `odoo_knowledge.py` provides Odoo domain knowledge via MCP server instructions
- Without `DATABASE_URL`: single-tenant mode (one Odoo instance from env vars, no OAuth)
- With `DATABASE_URL`: multi-tenant mode (requires odoo-mcp-pro-admin package)

## Open core boundary

The public repo works standalone for single-tenant use (stdio or HTTP + API key).
The SaaS features (multi-tenant, OAuth, admin UI, billing) live in the private
`odoo-mcp-pro-admin` package and are overlaid onto this package at build time.

**How the overlay works in production (Hetzner VPS):**
- Dockerfile entry point is `python -m mcp_server_odoo` (public)
- Admin repo's Dockerfile `COPY mcp_server_odoo_admin/` into
  `site-packages/mcp_server_odoo/admin/` before starting
- `DATABASE_URL` is set, which triggers public's `server.py` multi-tenant branch:
  imports `mcp_server_odoo.admin.db.DatabaseManager`, mounts `admin/app.create_admin_app` at `/admin`
- `oauth.py` (ZitadelTokenVerifier) and `registry.py` (ConnectionRegistry) are used
  by public's `run_http` when `DATABASE_URL` is set

**Implications for changes in this repo:**
- `server.py` multi-tenant branch (lines ~430-495), `oauth.py`, `registry.py`,
  and the registry-based paths in `tools.py` / `resources.py` are all production-critical.
  Do not remove or refactor without coordinating with the admin repo Dockerfile and deploy.
- `usage.py` has a stub `track_event` and `RateLimitExceeded` class. The admin package
  replaces this file at build time with the full `UsageTracker`. Keep the module-level
  API (`track_event`, `RateLimitExceeded`, `DEFAULT_DAILY_LIMIT`) stable.
- Admin imports from public: `config.OdooConfig`, `odoo_connection.OdooConnection`,
  `odoo_json2_connection.OdooJSON2Connection`, `performance.PerformanceManager`,
  `version_detect.detect_api_version`, `usage.track_event`. Breaking any of these
  signatures breaks admin.
- Dev overlay: admin's `local-dev.sh` symlinks admin code into `mcp_server_odoo/admin/`.
  These symlinks are `.gitignore`'d (or untracked) and should not be committed.

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
| `server.py` | Factory pattern, OAuth wiring, FastMCP setup |
| `tools.py` | MCP tools (search, create, update, delete, import) |
| `resources.py` | MCP resources (URI-based) |
| `schemas.py` | Pydantic result models |
| `odoo_json2_connection.py` | JSON/2 client (httpx, Odoo 19+) |
| `odoo_connection.py` | XML-RPC client (stdlib, Odoo 14-18) |
| `connection_protocol.py` | Protocol class for connection interface |
| `registry.py` | ConnectionRegistry -- maps users to Odoo connections |
| `oauth.py` | ZitadelTokenVerifier -- token introspection with caching |
| `odoo_knowledge.py` | Odoo domain knowledge (server instructions) |
| `config.py` | OdooConfig dataclass |
| `usage.py` | Usage tracking stub (full version in admin package) |
| `access_control.py` | Odoo ACL checks via check_access_rights |
