#!/usr/bin/env bash
# Setup Zitadel for MCP OAuth
#
# Usage:
#   bash setup-zitadel.sh                           # default: http://localhost:8085
#   bash setup-zitadel.sh https://auth.example.com  # production
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ZITADEL_URL="${1:-http://localhost:8085}"

# Find the PAT file
PAT_FILE=""
for candidate in \
  "$SCRIPT_DIR/machinekey/admin.pat" \
  "$SCRIPT_DIR/generated/machinekey/admin.pat"; do
  if [ -f "$candidate" ]; then
    PAT_FILE="$candidate"
    break
  fi
done

echo "=== Zitadel MCP OAuth Setup ==="
echo "  Zitadel URL: $ZITADEL_URL"
echo ""

# 1. Read admin PAT
if [ -z "$PAT_FILE" ]; then
  echo "ERROR: admin.pat not found."
  echo "Checked: $SCRIPT_DIR/machinekey/admin.pat"
  echo "         $SCRIPT_DIR/generated/machinekey/admin.pat"
  echo "Make sure Zitadel has been started with ZITADEL_FIRSTINSTANCE_PATPATH."
  exit 1
fi
ADMIN_TOKEN=$(cat "$PAT_FILE")
echo "Using admin PAT from $PAT_FILE"

# 2. Wait for Zitadel
echo "Waiting for Zitadel..."
for i in $(seq 1 30); do
  if curl -sf "$ZITADEL_URL/debug/ready" > /dev/null 2>&1; then
    echo "Zitadel is ready!"
    break
  fi
  if [ "$i" -eq 30 ]; then echo "ERROR: Zitadel not ready after 60s"; exit 1; fi
  sleep 2
done

# Helper
api() {
  local method="$1" path="$2"
  shift 2
  curl -sf "$ZITADEL_URL$path" \
    -X "$method" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    "$@"
}

# 3. Create project
echo ""
echo "Creating project 'MCP Server'..."
PROJECT_RESPONSE=$(api POST /management/v1/projects -d '{"name":"MCP Server"}')
PROJECT_ID=$(echo "$PROJECT_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  Project ID: $PROJECT_ID"

# 4. Create OIDC application (PKCE, for Claude.ai / Claude Code)
#
# Security notes:
# - authMethodType=NONE means this is a public client (no client_secret).
#   This is correct per OAuth 2.1 — Claude.ai is a browser app that cannot
#   store secrets. Security relies on PKCE (S256) instead.
# - devMode=false enforces strict redirect URI validation in production.
#   Set DEV_MODE=true in your environment for local development only.
echo ""
echo "Creating OIDC application 'mcp-client'..."
DEV_MODE="${DEV_MODE:-false}"
APP_RESPONSE=$(api POST "/management/v1/projects/$PROJECT_ID/apps/oidc" -d "{
  \"name\": \"mcp-client\",
  \"redirectUris\": [\"https://claude.ai/oauth/callback\", \"http://localhost:8000/oauth/callback\"],
  \"responseTypes\": [\"OIDC_RESPONSE_TYPE_CODE\"],
  \"grantTypes\": [\"OIDC_GRANT_TYPE_AUTHORIZATION_CODE\"],
  \"appType\": \"OIDC_APP_TYPE_WEB\",
  \"authMethodType\": \"OIDC_AUTH_METHOD_TYPE_NONE\",
  \"postLogoutRedirectUris\": [\"https://claude.ai\"],
  \"devMode\": $DEV_MODE
}")
CLIENT_ID=$(echo "$APP_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['clientId'])")
echo "  Client ID: $CLIENT_ID"

# 5. Create API application for token introspection
# NOTE: Zitadel requires an API application (not a machine user secret)
# for token introspection to work correctly.
echo ""
echo "Creating API application 'mcp-introspector'..."
INTROSPECT_RESPONSE=$(api POST "/management/v1/projects/$PROJECT_ID/apps/api" -d '{
  "name": "mcp-introspector",
  "authMethodType": "API_AUTH_METHOD_TYPE_BASIC"
}')
INTROSPECT_CLIENT_ID=$(echo "$INTROSPECT_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['clientId'])")
INTROSPECT_CLIENT_SECRET=$(echo "$INTROSPECT_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['clientSecret'])")
echo "  Introspection Client ID: $INTROSPECT_CLIENT_ID"

# 6. Output
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Set these env vars on your host before starting MCP servers:"
echo ""
echo "  export ZITADEL_CLIENT_ID=$INTROSPECT_CLIENT_ID"
echo "  export ZITADEL_CLIENT_SECRET=$INTROSPECT_CLIENT_SECRET"
echo ""
echo "Zitadel UI:      $ZITADEL_URL"
echo "MCP Client ID:   $CLIENT_ID  (configure in Claude.ai as OIDC client)"
echo "Project ID:      $PROJECT_ID"
echo ""
echo "To test token introspection:"
echo "  curl -s $ZITADEL_URL/oauth/v2/token \\"
echo "    --user '$INTROSPECT_CLIENT_ID:$INTROSPECT_CLIENT_SECRET' \\"
echo "    -d 'grant_type=client_credentials&scope=openid urn:zitadel:iam:org:project:id:$PROJECT_ID:aud'"
