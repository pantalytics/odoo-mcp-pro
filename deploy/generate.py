#!/usr/bin/env python3
"""Generate docker-compose.yml and Caddyfile from instances.yml."""

import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

DEPLOY_DIR = Path(__file__).parent
INSTANCES_FILE = DEPLOY_DIR / "instances.yml"
OUTPUT_DIR = DEPLOY_DIR / "generated"


def load_instances() -> dict:
    if not INSTANCES_FILE.exists():
        sys.exit(
            f"Error: {INSTANCES_FILE} not found.\n"
            f"Copy instances.example.yml to instances.yml and fill in your values."
        )
    with open(INSTANCES_FILE) as f:
        return yaml.safe_load(f)


def generate_compose(config: dict) -> str:
    services = {}
    volumes = {"caddy_data": {}, "caddy_config": {}}

    # --- Zitadel (optional) ---
    zitadel_cfg = config.get("zitadel")
    if zitadel_cfg:
        zitadel_domain = zitadel_cfg["domain"]

        services["zitadel-db"] = {
            "image": "postgres:16-alpine",
            "container_name": "zitadel-db",
            "restart": "unless-stopped",
            "environment": {
                "POSTGRES_USER": "zitadel",
                "POSTGRES_PASSWORD": "${ZITADEL_DB_PASSWORD:-zitadel}",
                "POSTGRES_DB": "zitadel",
            },
            "volumes": ["zitadel-db-data:/var/lib/postgresql/data"],
            "networks": ["mcp-net"],
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U zitadel"],
                "interval": "5s",
                "timeout": "3s",
                "retries": 5,
            },
        }
        volumes["zitadel-db-data"] = {}

        services["zitadel"] = {
            "image": "ghcr.io/zitadel/zitadel:latest",
            "container_name": "zitadel",
            "restart": "unless-stopped",
            "command": "start-from-init --masterkeyFromEnv --tlsMode disabled",
            "environment": {
                "ZITADEL_MASTERKEY": "${ZITADEL_MASTERKEY}",
                "ZITADEL_DATABASE_POSTGRES_HOST": "zitadel-db",
                "ZITADEL_DATABASE_POSTGRES_PORT": 5432,
                "ZITADEL_DATABASE_POSTGRES_DATABASE": "zitadel",
                "ZITADEL_DATABASE_POSTGRES_USER_USERNAME": "zitadel",
                "ZITADEL_DATABASE_POSTGRES_USER_PASSWORD": "${ZITADEL_DB_PASSWORD:-zitadel}",
                "ZITADEL_DATABASE_POSTGRES_USER_SSL_MODE": "disable",
                "ZITADEL_DATABASE_POSTGRES_ADMIN_USERNAME": "zitadel",
                "ZITADEL_DATABASE_POSTGRES_ADMIN_PASSWORD": "${ZITADEL_DB_PASSWORD:-zitadel}",
                "ZITADEL_DATABASE_POSTGRES_ADMIN_SSL_MODE": "disable",
                "ZITADEL_EXTERNALSECURE": "true",
                "ZITADEL_EXTERNALPORT": 443,
                "ZITADEL_EXTERNALDOMAIN": zitadel_domain,
                # Bootstrap: machine user + PAT on first init
                "ZITADEL_FIRSTINSTANCE_ORG_HUMAN_USERNAME": f"admin@{zitadel_domain}",
                "ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORD": "${ZITADEL_ADMIN_PASSWORD:-Password1!}",
                "ZITADEL_FIRSTINSTANCE_ORG_MACHINE_MACHINE_USERNAME": "zitadel-admin-sa",
                "ZITADEL_FIRSTINSTANCE_ORG_MACHINE_MACHINE_NAME": "Admin Service Account",
                "ZITADEL_FIRSTINSTANCE_ORG_MACHINE_PAT_EXPIRATIONDATE": "2030-01-01T00:00:00Z",
                "ZITADEL_FIRSTINSTANCE_PATPATH": "/machinekey/admin.pat",
            },
            "volumes": [
                "./machinekey:/machinekey",
                "./zitadel-ready.yaml:/zitadel-ready.yaml:ro",
            ],
            "depends_on": {
                "zitadel-db": {"condition": "service_healthy"},
            },
            "networks": ["mcp-net"],
            "healthcheck": {
                "test": [
                    "CMD", "/app/zitadel", "ready",
                    "--config", "/zitadel-ready.yaml",
                ],
                "interval": "10s",
                "timeout": "5s",
                "retries": 30,
                "start_period": "120s",
            },
        }

    # --- MCP server instances ---
    mcp_service_names = []
    for name, inst in config["instances"].items():
        svc_name = f"mcp-{name}"
        mcp_service_names.append(svc_name)

        env = {
            "ODOO_URL": inst["odoo_url"],
            "ODOO_DB": inst.get("odoo_db", ""),
            "ODOO_API_KEY": inst["odoo_api_key"],
            "ODOO_API_VERSION": inst.get("api_version", "json2"),
            "ODOO_YOLO": inst.get("yolo", "off"),
            "ODOO_MCP_TRANSPORT": "streamable-http",
            "ODOO_MCP_HOST": "0.0.0.0",
            "ODOO_MCP_PORT": "8000",
        }

        # OAuth env vars
        if "oauth" in config:
            oauth = config["oauth"]
            # Use internal Docker network for introspection when Zitadel
            # is in the same stack (avoids round-trip through Caddy/internet)
            if zitadel_cfg:
                introspection_url = "http://zitadel:8080/oauth/v2/introspect"
            else:
                introspection_url = oauth["introspection_url"]

            # RFC 9728: resource_server_url must be the full public MCP endpoint URL
            # so the SDK registers the PRM route at the correct path-based well-known URI
            resource_server_url = f"https://{config['domain']}/{name}/mcp"

            env.update({
                "OAUTH_ISSUER_URL": oauth["issuer_url"],
                "OAUTH_RESOURCE_SERVER_URL": resource_server_url,
                "ZITADEL_INTROSPECTION_URL": introspection_url,
                "ZITADEL_CLIENT_ID": "${ZITADEL_CLIENT_ID}",
                "ZITADEL_CLIENT_SECRET": "${ZITADEL_CLIENT_SECRET}",
            })

        service = {
            "build": {
                "context": "../..",
                "dockerfile": "Dockerfile",
                "args": {"GIT_COMMIT": "${GIT_COMMIT:-unknown}"},
            },
            "container_name": svc_name,
            "restart": "unless-stopped",
            "environment": env,
            "networks": ["mcp-net"],
            "extra_hosts": ["host.docker.internal:host-gateway"],
        }

        # If Zitadel is in the stack, MCP depends on it
        if zitadel_cfg:
            service["depends_on"] = {
                "zitadel": {"condition": "service_healthy"},
            }

        services[svc_name] = service

    # --- Caddy ---
    caddy_depends = list(mcp_service_names)
    if zitadel_cfg:
        caddy_depends.append("zitadel")

    services["caddy"] = {
        "image": "caddy:2-alpine",
        "container_name": "mcp-caddy",
        "restart": "unless-stopped",
        "ports": ["80:80", "443:443"],
        "volumes": [
            "./Caddyfile:/etc/caddy/Caddyfile:ro",
            "caddy_data:/data",
            "caddy_config:/config",
        ],
        "networks": ["mcp-net"],
        "depends_on": caddy_depends,
    }

    compose = {
        "services": services,
        "networks": {"mcp-net": {"driver": "bridge"}},
        "volumes": volumes,
    }

    return yaml.dump(compose, default_flow_style=False, sort_keys=False)


def generate_caddyfile(config: dict) -> str:
    lines = []

    # --- Zitadel domain ---
    zitadel_cfg = config.get("zitadel")
    if zitadel_cfg:
        zitadel_domain = zitadel_cfg["domain"]
        lines.append(f"{zitadel_domain} {{")
        lines.append("  reverse_proxy zitadel:8080")
        lines.append("}")
        lines.append("")

    # --- MCP domain ---
    domain = config["domain"]
    lines.append(f"{domain} {{")

    # OAuth well-known endpoints (RFC 8414 + RFC 9728)
    if "oauth" in config:
        first_svc = f"mcp-{list(config['instances'].keys())[0]}:8000"

        # RFC 8414: authorization server metadata (legacy fallback)
        lines.append("  handle /.well-known/oauth-authorization-server {")
        lines.append(f"    reverse_proxy {first_svc}")
        lines.append("  }")
        lines.append("")

        # RFC 9728: protected resource metadata
        # MCP clients request path-based URIs like:
        #   /.well-known/oauth-protected-resource/{instance}/mcp
        # Route each instance's PRM to the correct container
        for inst_name in config["instances"]:
            inst_svc = f"mcp-{inst_name}:8000"
            lines.append(f"  handle /.well-known/oauth-protected-resource/{inst_name}/* {{")
            lines.append(f"    reverse_proxy {inst_svc}")
            lines.append("  }")
            lines.append("")

        # Root-based fallback for PRM (when client retries without path)
        lines.append("  handle /.well-known/oauth-protected-resource {")
        lines.append(f"    reverse_proxy {first_svc}")
        lines.append("  }")
        lines.append("")

    for name in config["instances"]:
        lines.append(f"  handle_path /{name}/* {{")
        lines.append(f"    reverse_proxy mcp-{name}:8000")
        lines.append("  }")
        lines.append("")

    lines.append("  respond 404")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def main():
    config = load_instances()

    if "domain" not in config:
        sys.exit("Error: 'domain' is required in instances.yml")
    if "instances" not in config or not config["instances"]:
        sys.exit("Error: no instances defined in instances.yml")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    compose_path = OUTPUT_DIR / "docker-compose.yml"
    compose_content = generate_compose(config)
    compose_path.write_text(compose_content)

    caddyfile_path = OUTPUT_DIR / "Caddyfile"
    caddyfile_content = generate_caddyfile(config)
    caddyfile_path.write_text(caddyfile_content)

    # Generate Zitadel ready-check config if needed
    zitadel_cfg = config.get("zitadel")
    if zitadel_cfg:
        ready_yaml = OUTPUT_DIR / "zitadel-ready.yaml"
        ready_yaml.write_text(
            "# Healthcheck config for 'zitadel ready' behind TLS proxy\n"
            "TLS:\n"
            "  Enabled: false\n"
            "ExternalSecure: false\n"
            "ExternalPort: 8080\n"
            f"ExternalDomain: {zitadel_cfg['domain']}\n"
        )
        (OUTPUT_DIR / "machinekey").mkdir(exist_ok=True)

    print(f"Generated {compose_path}")
    print(f"Generated {caddyfile_path}")
    print()

    zitadel_cfg = config.get("zitadel")
    if zitadel_cfg:
        print(f"Zitadel:  https://{zitadel_cfg['domain']}")

    print("Instances:")
    for name in config["instances"]:
        print(f"  https://{config['domain']}/{name}/")
    print()
    print("To deploy:")
    print(f"  cd {OUTPUT_DIR}")
    if zitadel_cfg:
        print("  # Set required env vars:")
        print("  export ZITADEL_MASTERKEY=$(openssl rand -base64 24)  # 32+ chars")
        print("  export ZITADEL_CLIENT_ID=...    # from setup-zitadel.sh")
        print("  export ZITADEL_CLIENT_SECRET=... # from setup-zitadel.sh")
        print()
        print("  # Phase 1: Start Zitadel + Caddy")
        print("  docker compose up -d zitadel-db zitadel caddy")
        print("  # Wait for Zitadel to init, then run setup-zitadel.sh")
        print()
        print("  # Phase 2: Start MCP servers")
        print("  docker compose up -d --build")
    else:
        print("  docker compose up -d --build")


if __name__ == "__main__":
    main()
