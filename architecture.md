# Architecture

## Existing architecture (upstream ivnvxd/mcp-server-odoo)

```
Claude (LLM)
    │  MCP protocol (stdio or streamable-http)
    ▼
FastMCP (mcp library)
    │
    ├── OdooResourceHandler (resources.py)
    │     URI templates: odoo://{model}/record/{id}
    │                    odoo://{model}/search
    │                    odoo://{model}/count
    │                    odoo://{model}/fields
    │
    └── OdooToolHandler (tools.py)
          Tools: search_records, get_record, list_models,
                 create_record, update_record, delete_record,
                 list_resource_templates
    │
    ├── AccessController (access_control.py)
    │     YOLO mode: bypass or block writes
    │     Standard mode: HTTP GET /mcp/models/{model}/access
    │
    └── OdooConnection (odoo_connection.py)
          Protocol: XML-RPC (xmlrpc.client.ServerProxy)
          Endpoints: /xmlrpc/2/common  (auth)
                     /xmlrpc/2/object  (CRUD)
          Auth: uid + api_key or password via XML-RPC authenticate()
```

### Two operating modes

| | YOLO mode | Standard mode |
|--|-----------|---------------|
| XML-RPC endpoints | `/xmlrpc/2/*` | `/mcp/xmlrpc/*` |
| Access control | Bypassed | Per-model via REST `/mcp/models/` |
| Odoo module required | No | Yes (`mcp_server` Odoo app) |
| Auth | XML-RPC `common.authenticate()` | HTTP POST `/mcp/auth/validate` |

---

## Odoo 19 JSON/2 API

Introduced in Odoo 19. **XML-RPC and JSON-RPC are removed in Odoo 20.**

### Key differences from XML-RPC

| | XML-RPC | JSON/2 |
|--|---------|--------|
| Transport | XML over HTTP | JSON over HTTP |
| Endpoint | `/xmlrpc/2/object` | `/json/2/{model}/{method}` |
| Auth | uid + password/token as args | `Authorization: Bearer <api_key>` header |
| Arguments | Positional lists | Named JSON object |
| HTTP status | Always 200 | Real status codes (404, 500, ...) |
| Transactions | Manual | Auto-commit per call |
| Odoo version | 14+ | 19+ only |

### Example: search_read via JSON/2

```http
POST /json/2/res.partner/search_read
Authorization: Bearer <api_key>
Content-Type: application/json

{
  "domain": [["is_company", "=", true]],
  "fields": ["name", "email", "phone"],
  "limit": 10,
  "offset": 0
}
```

---

## Target architecture (this fork)

```
Claude (LLM)
    │
    ▼
FastMCP
    │
    ├── OdooResourceHandler  (unchanged)
    └── OdooToolHandler      (unchanged)
          │
          └── connection  ← factory in server.py picks the right one
                │
                ├── OdooConnection       (odoo_connection.py — unchanged)
                │     XML-RPC, Odoo 14-18, ODOO_API_VERSION=xmlrpc
                │
                └── OdooJSON2Connection  (odoo_json2_connection.py — NEW)
                      httpx, Odoo 19+, ODOO_API_VERSION=json2
```

### What changes

| File | Change |
|------|--------|
| `mcp_server_odoo/odoo_json2_connection.py` | **New** — JSON/2 client |
| `mcp_server_odoo/config.py` | Add `ODOO_API_VERSION: str = "xmlrpc"` |
| `mcp_server_odoo/server.py` | Factory: pick connection class based on config |
| Everything else | **Unchanged** |

### OdooJSON2Connection public interface (must match OdooConnection)

```python
class OdooJSON2Connection:
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

    # Properties used by tools/resources
    @property
    def uid(self) -> int
    @property
    def database(self) -> str
    @property
    def version(self) -> dict
```

---

## Existing codebase — class summary

### OdooConfig (config.py)
Dataclass loaded from env vars. Key fields:
- `url`, `database`, `api_key`, `username`, `password`
- `yolo_mode`: `"off"` | `"read"` | `"true"`
- `transport`: `"stdio"` | `"streamable-http"`
- `default_limit`, `max_limit`, `max_smart_fields`

### OdooConnection (odoo_connection.py)
XML-RPC client using stdlib `xmlrpc.client.ServerProxy`.
- Creates three proxies: `_db_proxy`, `_common_proxy`, `_object_proxy`
- All CRUD goes through `execute_kw(model, method, args, kwargs)`
- Field list caching via `fields_get()`

### AccessController (access_control.py)
- YOLO mode: allow all reads, block/allow writes per `yolo_mode` level
- Standard mode: HTTP GET `/mcp/models/{model}/access` with `X-API-Key`
- Results cached 300 seconds

### OdooMCPServer (server.py)
- Owns the `FastMCP` app instance
- Calls `_ensure_connection()` on startup
- Registers resources and tools

### OdooToolHandler (tools.py)
Seven tools registered via `@app.tool()` decorators.
Smart field selection when no fields specified (scores by business relevance).

### OdooResourceHandler (resources.py)
Four URI-addressed read-only endpoints.

### Schemas (schemas.py)
Pydantic output models: `SearchResult`, `RecordResult`, `CreateResult`,
`UpdateResult`, `DeleteResult`, `ModelsResult`, `ResourceTemplatesResult`.

---

## Ecosystem context

| Project | Stars | API | Status |
|---------|-------|-----|--------|
| ivnvxd/mcp-server-odoo | 160 | XML-RPC | Active (commits daily) |
| tuanle96/mcp-odoo | 273 | JSON-RPC | Stalled since Apr 2025 |
| twtrubiks/odoo19-mcp-server | 0 | JSON/2 | New, docs in Chinese |
| hachecito/odoo-mcp-improved | 34 | JSON-RPC | Dec 2025 |
| BACON-AI-CLOUD fork | — | XML-RPC | 17 tools, enterprise focus |

**This fork's unique position**: active community base (ivnvxd) + modern API (JSON/2) + forward-compatible (Odoo 20 ready).
