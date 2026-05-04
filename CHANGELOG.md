# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is the open-source MCP server. Hosted-service features (billing, team
management UI, admin dashboard, deploy infrastructure) live in the proprietary
`odoo-mcp-pro-admin` overlay and are not tracked here.

## [Unreleased]

### Fixed
- **XML-RPC username resolution**: `registry.get_connection` now reads `user_conn.odoo_login` (when set) and falls back to `user_conn.email`. Odoo authenticates against `res.users.login`, which can differ from the user's email (e.g. `login="admin"`); the previous behavior locked everyone to the sign-up email.
- **`server_info` observability**: `_current_sub` is now set before the registry/rate-limit calls in `_get_user_context`, so when those raise and a tool catches the exception (notably `server_info`), usage tracking still attributes the event to the correct user instead of silently no-op'ing as `"stdio"`.

## [1.5.0] - 2026-04-28

### Added
- **`set_binary_field`** tool: upload images and binaries to record fields by URL (e.g. `image_1920`, attachments). URL-only — `data:` URIs are rejected.
- Warning when writing `image_1920` on `product.product` falls through to the template.

## [1.4.3] - 2026-04-23

### Added
- `find_skill` tool: keyword-based skill discovery in a single call (avoids the `list_skills` + `get_skill` round-trip).

## [1.4.2] - 2026-04-23

### Added
- `list_skills` and `get_skill` exposed as MCP tools (in addition to `skill://` resources) so models can discover them when the client UI does not surface resources.

## [1.4.1] - 2026-04-23

### Fixed
- `skills/` directory now shipped inside the wheel.

## [1.4.0] - 2026-04-23

### Added
- **Skills**: Odoo workflow guides exposed as MCP resources via `skill://` URIs. First batch covers common patterns (CRM imports, partner upsert, sales orders).
- **ChatGPT connector support**: Dynamic Client Registration so ChatGPT connectors can self-register the OAuth app.

## [1.3.1] - 2026-04-17

### Changed
- License switched to **Elastic License 2.0**; open-core overlay relationship documented in README and SETUP.md.
- Documentation rewritten for open-core scope (admin/deploy content moved to the proprietary overlay).

## [1.3.0] - 2026-04-16

### Added
- **`import_records`**: idempotent upsert via Odoo's native `load()` using external IDs — re-running the same input does not create duplicates.
- Odoo domain knowledge included in MCP server instructions so models make better routing decisions (CRM vs Sales vs Subscriptions, etc.).

### Changed
- **Open-core split**: admin panel, billing, team management, and deploy infrastructure moved to the proprietary `odoo-mcp-pro-admin` overlay. This package now provides only the MCP server.
- Removed silent database guessing — database must be explicitly configured.

## [1.2.1] - 2026-04-11

### Added
- Token introspection caching (60s) with retry — fewer round-trips to the identity provider.
- Auth error pages instead of silent redirect loops on login failures.
- CORS handler on the OAuth callback route (fixes preflight returning 405).

### Changed
- Hardcoded identity-provider fallbacks removed: required env vars must be set explicitly.

## [1.2.0] - 2026-04-05

### Added
- PostHog server-side analytics for `mcp_tool_called` events. Opt-in via `POSTHOG_API_KEY`; disabled when running self-hosted without the env var.

### Changed
- Setup instructions expanded with the full Claude connector flow (Connect button, sign-in popup, OAuth approval).

### Fixed
- URL normalization: trailing slashes and paths (`/web`, `/odoo`) stripped on save for consistent matching.

## [1.1.3] - 2026-04-01

### Added
- Optional **database name** field for self-hosted Odoo where listing is blocked.
- **Test Connection** button with stored debug info for support.
- Step-by-step setup verification with a specific error per check.

### Fixed
- 404 on `check_access_rights` treated as allowed (Odoo.sh compatibility).
- Allow updating URL or database without re-entering the API key.

## [1.1.2] - 2026-03-31

### Added
- `company_id` included in smart fields; companies returned in `server_info`.

### Fixed
- Use `check_access_rights` for XML-RPC access control — no Odoo MCP module installation required.
- Fall back to standard XML-RPC when the MCP module is not installed.
- Auto-detect Odoo.sh database name when DB listing is blocked.
- Use authenticated email as Odoo username for XML-RPC auth (Odoo 14–18).

## [1.1.1] - 2026-03-31

### Fixed
- Use standard Odoo XML-RPC endpoints instead of the `/mcp/` prefix (drop the dependency on a server-side Odoo MCP module).
- Lowercase email for XML-RPC username with exact-then-lowercase fallback (Odoo stores logins lowercase).

## [1.1.0] - 2026-03-31

### Added
- **Bulk operations**: `create_records`, `update_records`, `delete_records` — operate on many records in a single call.

## [1.0.0] - 2026-03-27

### Added
- **HTTP transport** with OAuth 2.1 token introspection (PKCE).
- **Connection registry**: maps authenticated users to Odoo connections (Postgres-backed, with an in-process TTL cache).
- **`server_info`** tool: exposes server version and git commit hash.
- **Protected Resource Metadata** (RFC 9728) discovery for OAuth resource servers.

### Changed
- Architecture: from single-tenant stdio to multi-user HTTP server.
- `list_models` fetches from `ir.model` in JSON/2 mode and skips per-model permission checks for performance.
- Logout clears the upstream identity-provider session and shows the account picker on next login.

### Removed
- YOLO mode in favor of Odoo native permissions as the single source of truth.

### Note
- Admin panel, SaaS billing, team management, and deploy infrastructure shipped together with this version were later split into the proprietary `odoo-mcp-pro-admin` overlay (April 2026, see 1.3.0). This OSS package now provides only the MCP server.

## [0.4.0] - 2026-02-22

### Added
- **Structured output**: All tools return typed Pydantic models with auto-generated JSON schemas for MCP clients (`SearchResult`, `RecordResult`, `ModelsResult`, `CreateResult`, `UpdateResult`, `DeleteResult`)
- **Tool annotations**: All tools declare `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint` via MCP `ToolAnnotations`
- **Resource annotations**: All resources declare `audience` and `priority` via MCP `Annotations`
- **Human-readable titles**: All tools and resources include `title` for better display in MCP clients

### Changed
- **MCP SDK**: Upgraded from `>=1.9.4` to `>=1.26.0,<2`
- **`get_record` structured output**: Returns `RecordResult` with separate `record` and `metadata` fields instead of injecting `_metadata` into record data
- **Tooling**: Replace black/mypy with ruff format/ty for formatting and type checking

### Fixed
- **VertexAI compatibility**: Simplified `search_records` `domain`/`fields` type hints from `Union` to `Optional[Any]` to avoid `anyOf` JSON schemas rejected by VertexAI/Google ADK (#27)
- **Stale record data**: Removed record-level caching from `read()` to prevent returning stale field values (e.g. `active`) when records change in Odoo between calls (#28)
- **Tests**: Integration tests now use `ODOO_URL` for server detection, deduplicated server checks, fixed async test handling, updated assertions for structured output types, halved suite runtime

### Removed
- Legacy error type aliases (`ToolError`, `ResourceError`, `ResourceNotFoundError`, `ResourcePermissionError`) — use `ValidationError`, `NotFoundError`, `PermissionError` directly
- Unused `_setup_handlers()` method from `OdooMCPServer`

## [0.3.1] - 2026-02-21

### Fixed
- **Authentication bypass**: Add missing `@property` on `is_authenticated` — was always truthy as a method reference, bypassing auth guards

### Changed
- Update CI dependencies (black 26.1.0, GitHub Actions v6/v7)
- Server version test validates semver format instead of hardcoded value

## [0.3.0] - 2025-09-14

### Added
- **YOLO Mode**: Development mode for testing without MCP module installation
  - Read-Only: Safe demo mode with read-only access to all models
  - Full Access: Unrestricted access for development (never use in production)
  - Works with any standard Odoo instance via native XML-RPC endpoints

## [0.2.2] - 2025-08-04

### Added
- **Direct Record URLs**: Added `url` field to `create_record` and `update_record` responses for direct access to records in Odoo

### Changed
- **Minimal Response Fields**: Reduced `create_record` and `update_record` tool responses to return only essential fields (id, name, display_name) to minimize LLM context usage
- **Smart Field Optimization**: Implemented dynamic field importance scoring to reduce smart default fields to most essential across all models, with configurable limit via `ODOO_MCP_MAX_SMART_FIELDS`

## [0.2.1] - 2025-06-28

### Changed
- **Resource Templates**: Updated `list_resource_templates` tool to clarify that query parameters are not supported in FastMCP resources

## [0.2.0] - 2025-06-19

### Added
- **Write Operations**: Enabled full CRUD functionality with `create_record`, `update_record`, and `delete_record` tools (#5)

### Changed
- **Resource Simplification**: Removed query parameters from resource URIs due to FastMCP limitations - use tools for advanced queries (#4)

### Fixed
- **Domain Parameter Parsing**: Fixed `search_records` tool to accept both JSON strings and Python-style domain strings, supporting various format variations

## [0.1.2] - 2025-06-19

### Added
- **Resource Discovery**: Added `list_resource_templates` tool to provide resource URI template information
- **HTTP Transport**: Added streamable-http transport support for web and remote access

## [0.1.1] - 2025-06-16

### Fixed
- **HTTPS Connection**: Fixed SSL/TLS support by using `SafeTransport` for HTTPS URLs instead of regular `Transport`
- **Database Validation**: Skip database existence check when database is explicitly configured, as listing may be restricted for security

## [0.1.0] - 2025-06-08

### Added

#### Core Features
- **MCP Server**: Full Model Context Protocol implementation using FastMCP with stdio transport
- **Dual Authentication**: API key and username/password authentication
- **Resource System**: Complete `odoo://` URI schema with 5 operations (record, search, browse, count, fields)
- **Tools**: `search_records`, `get_record`, `list_models` with smart field selection
- **Auto-Discovery**: Automatic database detection and connection management

#### Data & Performance
- **LLM-Optimized Output**: Hierarchical text formatting for AI consumption
- **Connection Pooling**: Efficient connection reuse with health checks
- **Pagination**: Smart handling of large datasets
- **Caching**: Performance optimization for frequently accessed data
- **Error Handling**: Comprehensive error sanitization and user-friendly messages

#### Security & Access Control
- **Multi-layered Security**: Odoo permissions + MCP-specific access controls
- **Session Management**: Automatic credential injection and session handling
- **Audit Logging**: Complete operation logging for security

## Limitations
- **No Prompts**: Guided workflows not available
- **Alpha Status**: API may change before 1.0.0

**Note**: This alpha release provides production-ready data access for Odoo via AI assistants.
