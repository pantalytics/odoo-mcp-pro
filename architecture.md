# Architecture

Technical architecture of odoo-mcp-pro. For a product overview, see the [README](README.md).

> **Scope note**: this document describes the full hosted (multi-tenant) architecture.
> This repo ships only the **single-tenant** open-core package (one Odoo instance,
> API-key auth, no OAuth). The multi-tenant components below — Postgres tenant
> store, Zitadel/OAuth, ConnectionRegistry, admin panel — live in the private
> `odoo-mcp-pro-admin` package that powers the Pantalytics hosted service and
> cannot be deployed from this repo.

## Design principles

1. **Odoo + AI, samen sterker.** Combine the power of Odoo as an ERP with the power of AI as an interface. Don't replace Odoo -- make it more accessible.
2. **Use the interface that fits.** Some tasks are faster in the Odoo UI, others via a question to Claude. The user chooses.
3. **Odoo is the boss.** All data, permissions, and business logic live in Odoo. The MCP server is a stateless proxy.
4. **No setup barriers.** Self-service, auto-detection, minimal configuration. It should just work.
5. **Open and transparent.** Source-available (Elastic License 2.0), no vendor lock-in, standard protocols (MCP, OAuth 2.1).

---

## Overview

```
+-----------+     OAuth 2.1    +--------------+    JSON/2     +----------+
| Claude.ai |---------------->| MCP Server   |-------------->| Odoo     |
| (browser) |                 | (Docker)     |               | (cust A) |
+-----------+                 +------+-------+               +----------+
                                     |
                              +------+-------+
                              |  Postgres    |
                              |  tenants +   |
                              |  api_keys    |
                              +------+-------+
                                     |
                              +------+-------+
                              |  Zitadel     |
                              |  Cloud       |
                              |  (identity)  |
                              +--------------+
```

---

## Components

### MCP Server

Built on [FastMCP](https://github.com/modelcontextprotocol/python-sdk). Exposes 6 tools and 4 resources for Odoo data access. Routes each authenticated user to the correct Odoo instance based on their Zitadel organization.

### Postgres

Stores three tables (see [Data model](#data-model)).

### Zitadel Cloud

Managed identity provider. Handles:
- User authentication (OAuth 2.1 + PKCE)
- Organization management (one org per customer)
- Token issuance and introspection
- Optional federation (Microsoft Entra ID)

### Caddy

Reverse proxy with automatic TLS. Sits in front of the MCP server and admin panel. Proxies `/authorize`, `/token`, and `/register` to Zitadel for Claude.ai compatibility.

### Admin Panel

Routes mounted directly into the MCP SDK's Starlette app at `/admin` (not wrapped in a separate Starlette, to preserve the SDK's lifespan management). Provides:
- **Self-service setup** (`/admin/setup`): shows all tenants a user belongs to, each with its own API key form
- OAuth login via Zitadel (OIDC Authorization Code + PKCE)
- Logout clears Zitadel session and shows account picker on next login

---

## Data model

### `tenants`

Each tenant represents one Odoo instance linked to a Zitadel organization.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `name` | TEXT | Display name |
| `slug` | TEXT | URL-friendly identifier (unique) |
| `zitadel_org_id` | TEXT | Zitadel organization ID (unique) |
| `odoo_url` | TEXT | Odoo instance URL |
| `odoo_db` | TEXT | Odoo database name (empty for Odoo.sh) |
| `api_version` | TEXT | `json2` or `xmlrpc` (auto-detected in future) |
| `is_active` | BOOLEAN | Soft delete flag |

### `user_connections`

Each row maps a Zitadel user to a tenant with their Odoo API key.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `zitadel_sub` | TEXT | Zitadel subject ID |
| `email` | TEXT | User email (informational) |
| `tenant_id` | INTEGER | FK to tenants |
| `odoo_api_key` | TEXT | Encrypted Odoo API key (Fernet/AES-128) |
| `is_active` | BOOLEAN | Soft delete flag |

Unique constraint on `(zitadel_sub, tenant_id)`.

### `admins`

Super admins (Pantalytics) who can manage all tenants.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `zitadel_sub` | TEXT | Zitadel subject ID (unique) |
| `email` | TEXT | Admin email |

---

## Auth flows

### MCP tool call

```
Claude.ai
    |
    | 1. POST /mcp (tool call)
    |    Authorization: Bearer <user_token>
    v
FastMCP (BearerAuthBackend)
    |
    | 2. Token introspection -> Zitadel
    |    POST /oauth/v2/introspect (Basic Auth: service_client)
    |    Response includes: sub, org_id, active=true
    |
    | 3. ConnectionRegistry.get_connection(sub, org_id)
    |    - Look up tenant by org_id
    |    - Look up user_connection (sub + tenant_id)
    |    - Create/cache OdooJSON2Connection with user's API key
    |
    | 4. Execute tool (search_records, get_record, etc.)
    |    POST /json/2/{model}/{method}
    |    Authorization: Bearer <user_api_key>
    v
Odoo instance
```

### Admin panel login

```
User -> /admin/login -> Zitadel (OIDC + PKCE) -> /admin/callback
    |
    | Extract from userinfo:
    |   sub, email, org_id, org_name
    |
    | Check admins table:
    |   admin? -> /admin/ (dashboard)
    |   user?  -> /admin/setup (self-service)
```

### OAuth discovery (Claude.ai)

The MCP server serves Protected Resource Metadata (PRM) that points directly to Zitadel as the authorization server and includes the OIDC app Client ID (`MCP_OIDC_CLIENT_ID`). Claude.ai discovers Zitadel's endpoints via OIDC discovery fallback (`/.well-known/openid-configuration`).

**Known limitation**: Claude.ai can only have one active Odoo connector per browser session because Zitadel reuses the existing session. Super admins who need to access multiple orgs should use separate Zitadel accounts.

---

## User onboarding flow

1. **Admin** creates a Zitadel organization for the customer
2. **Admin** creates a tenant in the admin panel (name, Odoo URL, org ID)
3. **Admin** shares the setup link with the customer
4. **User** opens setup link, logs in with their company account
5. **User** enters their Odoo API key for each tenant they belong to
6. **User** adds the MCP server URL to Claude.ai
7. Claude authenticates via OAuth, MCP server routes to the right Odoo

---

## Deployment

Docker Compose with three containers:

| Container | Role |
|-----------|------|
| `mcp-server` | FastMCP + admin panel (Python) |
| `mcp-postgres` | Tenant config, user connections, admins |
| `mcp-caddy` | Reverse proxy, TLS, OAuth route proxying |

Plus Zitadel Cloud (external, not in Docker Compose).

See [SETUP.md](SETUP.md) for the full deployment guide.

---

## Connection layer

Abstracted behind `OdooConnectionProtocol`. Factory pattern in `server.py`:

```
Odoo 19+   ->  OdooJSON2Connection   (JSON/2 API, httpx)
Odoo 14-18 ->  OdooConnection        (XML-RPC, stdlib)
```

`ConnectionRegistry` creates and caches connections per user. Connections are evicted after 30 minutes of inactivity.

### JSON/2 API

| Aspect | Details |
|--------|---------|
| Endpoint | `POST /json/2/{model}/{method}` |
| Auth | `Authorization: Bearer <api_key>` |
| Database | `X-Odoo-Database: <db>` header |
| Body | Flat JSON with named args |
| Create/write | Use `vals` (not `values`) |
| Response | Raw JSON (no RPC envelope) |
| Errors | HTTP status codes (401, 403, 404, 422, 500) |

---

## Access control

The MCP server checks Odoo ACLs before sending requests:

```
POST /json/2/{model}/check_access_rights
{"operation": "read", "raise_exception": false}
-> true / false
```

Results are cached per model for 5 minutes. This prevents unexpected 403s and gives clear error messages.

---

## Key files (this repo)

| File | Role |
|------|------|
| `server.py` | Entry point, factory pattern, FastMCP setup |
| `odoo_json2_connection.py` | JSON/2 client (httpx, Odoo 19+) |
| `odoo_connection/` | XML-RPC client (stdlib, Odoo 14-18): core, auth, orm |
| `connection_protocol.py` | Protocol class defining the connection interface |
| `config.py` | OdooConfig dataclass, loaded from env vars |
| `tools/` | MCP tools (mixin package on `OdooToolHandler`) |
| `resources/` | MCP resources (URI-based read access) |
| `access_control.py` | Access control via check_access_rights |

The multi-tenant pieces (`ConnectionRegistry`, ZitadelTokenVerifier, admin
panel, tenant database) live in the private `odoo-mcp-pro-admin` package.

All source files live in `mcp_server_odoo/`. Tests in `tests/`.
