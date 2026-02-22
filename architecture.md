# Architecture

This document describes the technical architecture of odoo-mcp-pro. For a quick overview
and getting started, see the [README](README.md).

## High-level overview

odoo-mcp-pro is an MCP server built on [FastMCP](https://github.com/modelcontextprotocol/python-sdk)
that exposes Odoo ERP data to AI assistants. It supports two deployment modes and two Odoo API protocols:

| | Local | Cloud |
|--|-------|-------|
| **Transport** | stdio | streamable-http |
| **Auth** | None (local process) | OAuth 2.1 via Zitadel |
| **Clients** | Claude Code, Claude Desktop | Claude.ai, Claude Code |
| **Hosting** | Your machine | VPS + Caddy + Docker |

| | JSON/2 (default) | XML-RPC (legacy) |
|--|-------------------|-------------------|
| **Odoo versions** | 19+ | 14-18 |
| **Odoo 20 ready** | Yes | No (XML-RPC removed) |
| **Transport** | JSON over HTTP | XML over HTTP |
| **Auth** | Bearer token header | uid + password as args |
| **Module required** | No | Optional (for ACLs) |

---

## Request flow

```
   Client (Claude.ai / Claude Code / Claude Desktop)
       │
       │  MCP protocol (stdio or streamable-http)
       │
       ▼
   FastMCP framework
       │
       ├──▶ OAuth middleware (cloud only)
       │       │
       │       │  Token introspection (RFC 7662)
       │       ▼
       │     Zitadel ─── valid? ─── continue / reject
       │
       ├──▶ AccessController
       │       │
       │       ├── YOLO mode: allow all / read-only / block writes
       │       ├── Standard mode: check /mcp/models/{model}/access
       │       └── JSON/2 mode: delegate to Odoo's ACLs
       │
       ├──▶ OdooToolHandler (6 tools)
       │       search_records, get_record, list_models,
       │       create_record, update_record, delete_record
       │
       ├──▶ OdooResourceHandler (4 resources)
       │       odoo://{model}/record/{id}
       │       odoo://{model}/search
       │       odoo://{model}/count
       │       odoo://{model}/fields
       │
       └──▶ Connection (via factory)
               │
               ├── OdooJSON2Connection → POST /json/2/{model}/{method}
               └── OdooConnection      → execute_kw via XML-RPC
               │
               ▼
           Odoo instance
```

---

## Connection layer

The connection layer is abstracted behind `OdooConnectionProtocol` (Protocol class).
`server.py` uses a factory pattern at startup:

```
ODOO_API_VERSION=json2   →  OdooJSON2Connection   (recommended for Odoo 19+)
ODOO_API_VERSION=xmlrpc  →  OdooConnection        (backwards-compatible, Odoo 14-18)
```

Both expose the same interface:

```python
class OdooConnectionProtocol:
    def connect(self) -> None
    def disconnect(self) -> None
    def authenticate(self) -> None

    def search(self, model, domain, **kwargs) -> list[int]
    def read(self, model, ids, fields) -> list[dict]
    def search_read(self, model, domain, fields, **kwargs) -> list[dict]
    def search_count(self, model, domain) -> int
    def fields_get(self, model, attributes) -> dict
    def create(self, model, values) -> int
    def write(self, model, ids, values) -> bool
    def unlink(self, model, ids) -> bool

    @property
    def uid(self) -> int
    @property
    def database(self) -> str
    @property
    def version(self) -> dict
```

### JSON/2 API details

The Odoo 19 JSON/2 API is a modern REST-style API replacing both XML-RPC and JSON-RPC.

| Aspect | Details |
|--------|---------|
| Endpoint | `POST /json/2/{model}/{method}` |
| Auth | `Authorization: Bearer <api_key>` |
| Database | `X-Odoo-Database: <db>` header |
| Body | Flat JSON with named args |
| Create/write | Use `vals` (not `values`) |
| IDs | Top-level `ids` key |
| Response | Raw JSON (no RPC envelope) |
| Errors | HTTP status codes (401, 403, 404, 422, 500) |

Example request:
```http
POST /json/2/res.partner/search_read
Authorization: Bearer <api_key>
Content-Type: application/json

{
  "domain": [["is_company", "=", true]],
  "fields": ["name", "email", "phone"],
  "limit": 10
}
```

---

## OAuth 2.1 (cloud deployments)

When `OAUTH_ISSUER_URL` is configured, FastMCP enables OAuth middleware for secure
multi-user access. This is used for Claude.ai and remote Claude Code connections.

### Auth flow

```
Claude.ai
    │
    │  1. POST /mcp → 401 Unauthorized
    │     WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource"
    │
    │  2. Browser → Zitadel login (PKCE flow)
    │
    │  3. Authorization: Bearer <user_token>
    │
    ▼
FastMCP (BearerAuthBackend + RequireAuthMiddleware)
    │
    │  4. Token introspection → Zitadel
    │     POST /oauth/v2/introspect (Basic Auth: client_id:client_secret)
    │
    │  5. Active? → proceed to Odoo (using ODOO_API_KEY from env)
    │
    ▼
OdooJSON2Connection → Odoo 19
```

### Security model

| Property | Implementation |
|----------|---------------|
| Client ↔ MCP auth | OAuth 2.1 Bearer tokens |
| MCP ↔ Odoo auth | Server-side API key (env var) |
| Token validation | Zitadel introspection (RFC 7662) |
| Key exposure | Odoo API key never leaves the server |
| User isolation | Per-user tokens, audit via Zitadel |
| Optional | Omit `OAUTH_ISSUER_URL` for local/stdio use |

---

## Deployment architectures

### Local (stdio)

The simplest setup. Claude Code or Claude Desktop spawns the MCP server as a subprocess.
No hosting, no OAuth, no Docker.

```
Claude Code ──stdio──▶ odoo-mcp-pro (local process) ──▶ Odoo
```

### Cloud (VPS + Caddy + Docker)

For Claude.ai or multi-user access. Each Odoo instance runs as a separate MCP container
behind Caddy, with OAuth protecting access.

```
Claude.ai / Claude Code
        │
        │  HTTPS + OAuth 2.1
        │
        ▼
VPS (mcp.example.com)
   Caddy :443 (auto TLS via Let's Encrypt)
        │
        ├── /production/  →  mcp-production:8000  →  Odoo (odoo.sh)
        ├── /staging/     →  mcp-staging:8000     →  Odoo (staging)
        └── /local/       →  mcp-local:8000       →  Odoo (self-hosted)
                                    │
                                    │ Token introspection
                                    ▼
                              Zitadel (auth.example.com)
```

**Why a separate VPS?** Odoo.sh is a managed platform — no custom processes, no open ports.
The MCP server is a lightweight proxy; a small VPS (~€4/m) handles it easily.

### Deployment files

```
deploy/
├── instances.example.yml   # template — copy to instances.yml
├── generate.py             # generates docker-compose.yml + Caddyfile
└── generated/              # output (.gitignored)
    ├── docker-compose.yml
    └── Caddyfile
```

Container config per instance:
```bash
ODOO_URL=https://your-odoo.odoo.sh
ODOO_API_KEY=...                          # server-side only
ODOO_DB=...
ODOO_API_VERSION=json2
ODOO_MCP_TRANSPORT=streamable-http
ODOO_MCP_HOST=0.0.0.0
ODOO_MCP_PORT=8000
OAUTH_ISSUER_URL=https://auth.example.com
ZITADEL_INTROSPECTION_URL=https://auth.example.com/oauth/v2/introspect
ZITADEL_CLIENT_ID=mcp-server@project
ZITADEL_CLIENT_SECRET=...
```

---

## Key files

| File | Role |
|------|------|
| `server.py` | Entry point — factory pattern, OAuth wiring, FastMCP setup |
| `connection_protocol.py` | Protocol class defining the connection interface |
| `odoo_json2_connection.py` | JSON/2 client (httpx, Odoo 19+) |
| `odoo_connection.py` | XML-RPC client (stdlib, Odoo 14-18) |
| `oauth.py` | `ZitadelTokenVerifier` — token validation via introspection |
| `config.py` | `OdooConfig` dataclass, loaded from env vars |
| `tools.py` | 6 MCP tools with smart field selection |
| `resources.py` | 4 MCP resources (URI-based read access) |
| `access_control.py` | YOLO / standard / JSON/2 access control |
| `schemas.py` | Pydantic output models |
| `formatters.py` | Result formatting utilities |
| `error_handling.py` | Structured error handling |
| `error_sanitizer.py` | Strips sensitive info from error messages |
| `performance.py` | Performance tracking utilities |
| `logging_config.py` | Logging configuration |

All source files live in `mcp_server_odoo/`. Tests in `tests/` (34 test files).

---

## Smart field selection

When no specific fields are requested, the tool handler automatically selects the most
relevant fields for a model. Fields are scored by business relevance (name, email, phone
rank higher than internal IDs). This prevents overwhelming Claude with hundreds of fields
while ensuring useful results out of the box.
