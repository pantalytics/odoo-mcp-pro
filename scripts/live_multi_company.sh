#!/usr/bin/env bash
# Run tests/test_companies_live.py against a real Odoo 18, 19 and 20 in Docker.
#
#   bash scripts/live_multi_company.sh          # all three
#   bash scripts/live_multi_company.sh 19       # one version
#
# Each version gets a fresh database with Accounting and a generic chart of
# accounts, and an admin API key. Odoo 18 is reached over XML-RPC with the
# admin password, 19 and 20 over JSON/2 with the key. Containers and the
# network are removed at the end.
set -euo pipefail

if [ $# -gt 0 ]; then VERSIONS=("$@"); else VERSIONS=(18 19 20); fi
NET=mcp-live-mc
PG=mcp-live-pg
cleanup() {
  for v in "${VERSIONS[@]}"; do docker rm -f "mcp-live-odoo$v" >/dev/null 2>&1 || true; done
  docker rm -f "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo \
  -e POSTGRES_DB=postgres postgres:16 >/dev/null
sleep 5

odoo() { local v=$1; shift; docker run --rm -i --network "$NET" -e HOST="$PG" -e USER=odoo -e PASSWORD=odoo "odoo:$v" odoo "$@"; }

status=0
for v in "${VERSIONS[@]}"; do
  db="live$v"
  odoo "$v" -d "$db" -i account --without-demo=all --stop-after-init --log-level=warn >/dev/null
  # Odoo 20 matches the key's scope exactly; 18 and 19 want a global key.
  scope=None; [ "$v" -ge 20 ] && scope='"rpc"'
  key=$(printf 'import datetime\nfor c in env["res.company"].search([]):\n    env["account.chart.template"].try_loading("generic_coa", company=c, install_demo=False)\nk=env["res.users.apikeys"].with_user(env.ref("base.user_admin"))._generate(%s, "live", datetime.datetime.now()+datetime.timedelta(days=1))\nenv.cr.commit()\nprint("KEY", k)\n' "$scope" \
    | odoo "$v" shell -d "$db" --no-http --log-level=error | awk '/^KEY/{print $2}')
  port=$((18000 + v))
  docker run -d --name "mcp-live-odoo$v" --network "$NET" -p "$port:8069" -e HOST="$PG" -e USER=odoo \
    -e PASSWORD=odoo "odoo:$v" odoo -d "$db" --db-filter="^$db\$" >/dev/null
  for _ in $(seq 30); do curl -sf "http://localhost:$port/web/login" >/dev/null && break; sleep 2; done
  # 18 has no JSON/2: XML-RPC with the admin password, no key.
  [ "$v" -lt 19 ] && key=""
  echo "=== Odoo $v"
  ODOO_URL="http://localhost:$port" ODOO_DB="$db" ODOO_API_KEY="$key" ODOO_USER=admin ODOO_PASSWORD=admin \
    python -m pytest tests/test_companies_live.py -q -p no:cacheprovider || status=1
done
exit $status
