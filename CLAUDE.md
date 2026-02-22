# CLAUDE.md — Project context for Claude Code

## What this project is

A fork of [ivnvxd/mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) (MPL-2.0).

Goal: add support for the **Odoo 19 JSON/2 API** alongside the existing XML-RPC client,
then decide whether to PR upstream or publish as a standalone project.

## Current state

- Cloned from upstream on 2026-02-22 (upstream was active that same day)
- Working branch: `feature/json2-client` (to be created)
- No changes made yet — codebase is identical to upstream

## Architecture overview

See [architecture.md](architecture.md) for the full breakdown.

Key insight: the Odoo client is fully isolated in `mcp_server_odoo/odoo_connection.py`.
Tools and resources never touch the transport layer directly. This means we can add a
new `OdooJSON2Connection` class with the same public interface and swap it in at startup
without touching tools.py, resources.py, or schemas.py.

## Key files

| File | Role |
|------|------|
| `mcp_server_odoo/odoo_connection.py` | XML-RPC client — the main thing we're replacing |
| `mcp_server_odoo/server.py` | Wires everything together, decides which client to use |
| `mcp_server_odoo/config.py` | OdooConfig dataclass — add `ODOO_API_VERSION` here |
| `mcp_server_odoo/tools.py` | MCP tool definitions — should not need changes |
| `mcp_server_odoo/access_control.py` | YOLO mode permission checks |

## What we're adding

`mcp_server_odoo/odoo_json2_connection.py` — new file implementing `OdooJSON2Connection`:
- Same public interface as `OdooConnection`
- Uses `httpx` (already a dependency) instead of `xmlrpc.client`
- Auth via `Authorization: Bearer <api_key>` header
- Calls `POST /json/2/{model}/{method}` with named arguments in JSON body
- Odoo 19+ only

## Config

New env var: `ODOO_API_VERSION=json2` (default: `xmlrpc` for backward compat)

## Development setup

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Testing

```bash
pytest tests/
```

Integration tests require a live Odoo 19 instance with `ODOO_URL`, `ODOO_DB`, `ODOO_API_KEY` set.

## Conventions

- Follow existing code style (ruff is configured in pyproject.toml)
- Keep the JSON/2 client in a separate file — do not modify odoo_connection.py
- All public methods on OdooJSON2Connection must match OdooConnection's interface exactly
- No new dependencies unless strictly necessary (httpx is already available)

## Decision point (later)

After JSON/2 works and is tested:
- Option A: PR to ivnvxd with auto-detection (JSON/2 if Odoo ≥19, else XML-RPC)
- Option B: Publish as separate package `mcp-server-odoo19`
