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
    for name, inst in config["instances"].items():
        service = {
            "build": {"context": "../..", "dockerfile": "Dockerfile"},
            "container_name": f"mcp-{name}",
            "restart": "unless-stopped",
            "environment": {
                "ODOO_URL": inst["odoo_url"],
                "ODOO_DB": inst["odoo_db"],
                "ODOO_API_KEY": inst["odoo_api_key"],
                "ODOO_API_VERSION": inst.get("api_version", "json2"),
                "ODOO_MCP_TRANSPORT": "streamable-http",
                "ODOO_MCP_HOST": "0.0.0.0",
                "ODOO_MCP_PORT": "8000",
            },
            "networks": ["mcp-net"],
        }
        # Allow containers to reach services on the Docker host
        service["extra_hosts"] = ["host.docker.internal:host-gateway"]
        services[f"mcp-{name}"] = service

    caddy_service = {
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
        "depends_on": list(services.keys()),
    }
    services["caddy"] = caddy_service

    compose = {
        "services": services,
        "networks": {"mcp-net": {"driver": "bridge"}},
        "volumes": {"caddy_data": {}, "caddy_config": {}},
    }

    return yaml.dump(compose, default_flow_style=False, sort_keys=False)


def generate_caddyfile(config: dict) -> str:
    domain = config["domain"]
    lines = [f"{domain} {{"]

    for name in config["instances"]:
        lines.append(f"  handle_path /{name}/* {{")
        lines.append(f"    reverse_proxy mcp-{name}:8000")
        lines.append("  }")
        lines.append("")

    # Fallback: return 404 for unmatched paths
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

    print(f"Generated {compose_path}")
    print(f"Generated {caddyfile_path}")
    print()
    print("Instances:")
    for name in config["instances"]:
        print(f"  https://{config['domain']}/{name}/")
    print()
    print("To deploy:")
    print(f"  cd {OUTPUT_DIR}")
    print("  docker compose up -d --build")


if __name__ == "__main__":
    main()
