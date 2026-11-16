#!/usr/bin/env bash
# Run tests/test_companies_live.py against a real Odoo 18, 19 and 20 in Docker.
#
#   bash scripts/live_multi_company.sh          # all three
#   bash scripts/live_multi_company.sh 19       # one version
#
# Each version gets a fresh database with Accounting: the admin's company A,
# a company B, both with a generic chart of accounts and both in the admin's
# companies, and a company C the admin is not in. Odoo 18 is reached over
# XML-RPC with the admin password, 19 and 20 over JSON/2 with an admin API key.
# Odoo listens on 127.0.0.1 only. A version passes only when every test ran:
# a skip counts as a failure. Containers and the network go at the end.
set -euo pipefail

if [ $# -gt 0 ]; then VERSIONS=("$@"); else VERSIONS=(18 19 20); fi
for v in "${VERSIONS[@]}"; do
  [[ "$v" =~ ^[0-9]+$ ]] || { echo "version must be a number like 19, got '$v'" >&2; exit 2; }
done
NET=mcp-live-mc-$$
PG=mcp-live-pg-$$
cleanup() {
  for v in "${VERSIONS[@]}"; do docker rm -f "mcp-live-odoo$v-$$" >/dev/null 2>&1 || true; done
  docker rm -f "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo \
  -e POSTGRES_DB=postgres postgres:16 >/dev/null
for _ in $(seq 30); do docker exec "$PG" pg_isready -U odoo >/dev/null 2>&1 && break; sleep 1; done
docker exec "$PG" pg_isready -U odoo >/dev/null || { echo "Postgres did not start" >&2; exit 1; }

odoo() { local v=$1; shift; docker run --rm -i --network "$NET" -e HOST="$PG" -e USER=odoo -e PASSWORD=odoo "odoo:$v" odoo "$@"; }

# The companies and the key. Odoo 20 matches a key's scope exactly; 18 and 19
# want a global key (no scope).
setup_py() {
  cat <<EOF
import datetime
admin = env.ref("base.user_admin")
a = admin.company_id
b = env["res.company"].create({"name": "Company B", "currency_id": a.currency_id.id})
c = env["res.company"].create({"name": "Company C", "currency_id": a.currency_id.id})
for company in (a, b):
    env["account.chart.template"].try_loading("generic_coa", company=company, install_demo=False)
admin.write({"company_ids": [(6, 0, [a.id, b.id])], "company_id": a.id})
key = env["res.users.apikeys"].with_user(admin)._generate($1, "live", datetime.datetime.now() + datetime.timedelta(days=1))
env.cr.commit()
print("KEY", key)
EOF
}

status=0
for v in "${VERSIONS[@]}"; do
  db="live$v"
  odoo "$v" -d "$db" -i account --without-demo=all --stop-after-init --log-level=warn >/dev/null
  scope=None; [ "$v" -ge 20 ] && scope='"rpc"'
  key=$(setup_py "$scope" | odoo "$v" shell -d "$db" --no-http --log-level=error | awk '/^KEY/{print $2}')
  [ -n "$key" ] || { echo "Odoo $v: no API key generated" >&2; status=1; continue; }
  port=$((18000 + v))
  docker run -d --name "mcp-live-odoo$v-$$" --network "$NET" -p "127.0.0.1:$port:8069" -e HOST="$PG" \
    -e USER=odoo -e PASSWORD=odoo "odoo:$v" odoo -d "$db" --db-filter="^$db\$" >/dev/null
  up=0
  for _ in $(seq 60); do curl -sf "http://127.0.0.1:$port/web/login" >/dev/null && { up=1; break; }; sleep 2; done
  [ "$up" = 1 ] || { echo "Odoo $v did not come up on port $port" >&2; status=1; continue; }
  # 18 has no JSON/2: XML-RPC with the admin password, no key.
  [ "$v" -lt 19 ] && key=""
  echo "=== Odoo $v"
  out=$(MCP_LIVE_MULTI_COMPANY=1 ODOO_URL="http://127.0.0.1:$port" ODOO_DB="$db" ODOO_API_KEY="$key" \
    ODOO_USER=admin ODOO_PASSWORD=admin python -m pytest tests/test_companies_live.py -q -rs \
    -p no:cacheprovider 2>&1) || status=1
  echo "$out" | grep -E "passed|failed|skipped|^(FAILED|ERROR|SKIPPED)" || echo "$out" | tail -5
  if echo "$out" | grep -q "skipped"; then echo "Odoo $v: a test was skipped" >&2; status=1; fi
done
exit $status
