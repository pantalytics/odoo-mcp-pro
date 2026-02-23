# Setup Guide — odoo-mcp-pro

Step-by-step guide to connect Claude to your Odoo ERP.

## Choose your path

```
Do you want Claude.ai (web) or team access?
│
├── No → Option A: Local Setup (5 min)
│        Works with Claude Code and Claude Desktop.
│
└── Yes → Do you want to manage your own auth server?
          │
          ├── Yes → Option B: Self-hosted Zitadel (2-3 hrs first time)
          │         Full control. Requires VPS + Docker + DNS.
          │
          └── No  → Option C: Zitadel Cloud (1-2 hrs first time)
                    Managed auth. Simpler setup and maintenance.
```

---

## Table of Contents

1. [What You'll Need](#1-what-youll-need)
2. [Option A: Local Setup (fastest)](#2-option-a-local-setup)
3. [Option B: Cloud with Self-hosted Zitadel](#3-option-b-cloud-deployment-on-hetzner-vps)
4. [Option C: Cloud with Zitadel Cloud (simpler)](#4-option-c-cloud-with-zitadel-cloud)
5. [Microsoft Entra ID Federation (optional)](#5-microsoft-entra-id-federation)
6. [Generating an Odoo API Key](#6-how-to-generate-an-odoo-api-key)
7. [Day-to-Day Operations](#7-day-to-day-operations)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. What You'll Need

Before you begin, make sure you have:

### For local setup (Option A)
- [ ] **An Odoo instance** — Odoo 19+ (for JSON/2 API) or Odoo 14-18 (for XML-RPC)
- [ ] **An Odoo API key** — see [Section 6](#6-how-to-generate-an-odoo-api-key)
- [ ] **Claude Code** or **Claude Desktop** installed
- [ ] Your Odoo **database name** (check Odoo URL bar or Settings > Database)

### For cloud setup (Option B) — all of the above, plus:
- [ ] **A Hetzner Cloud account** — free to create at https://console.hetzner.cloud
- [ ] **A domain name** you control (e.g., `example.com`) with access to DNS settings
- [ ] **A credit card** for Hetzner (~€4.50/month)

> **Don't have an Odoo API key yet?** Jump to [Section 6](#6-how-to-generate-an-odoo-api-key) first, then come back here.

---

## 2. Option A: Local Setup

Local setup runs the MCP server on your own machine. Claude communicates with it
directly — no internet, no authentication needed. Best for personal use.

### A1: Claude Code (one command)

Open your terminal and run:

```bash
claude mcp add odoo-mcp-pro \
  -e ODOO_URL=http://localhost:8069 \
  -e ODOO_DB=your_database \
  -e ODOO_API_KEY=your_api_key \
  -e ODOO_API_VERSION=json2 \
  -- uvx odoo-mcp-pro
```

**Replace the values:**
- `ODOO_URL` — your Odoo instance URL (e.g., `http://localhost:8069` or `https://mycompany.odoo.com`)
- `ODOO_DB` — your database name
- `ODOO_API_KEY` — the API key you generated in Odoo
- `ODOO_API_VERSION` — use `json2` for Odoo 19+, or `xmlrpc` for Odoo 14-18

**Verify it works:** Open Claude Code and ask:

```
List all available Odoo models
```

If you see a list of models, you're done!

### A2: Claude Desktop

1. Open the Claude Desktop config file:
   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
   - **Linux**: `~/.config/Claude/claude_desktop_config.json`

2. Add this JSON (create the file if it doesn't exist):

```json
{
  "mcpServers": {
    "odoo": {
      "command": "uvx",
      "args": ["odoo-mcp-pro"],
      "env": {
        "ODOO_URL": "http://localhost:8069",
        "ODOO_DB": "your_database",
        "ODOO_API_KEY": "your_api_key",
        "ODOO_API_VERSION": "json2"
      }
    }
  }
}
```

3. Replace the values with your actual Odoo credentials.
4. **Fully quit and restart Claude Desktop** (not just close the window).
5. You should see a hammer icon in the input bar — that means MCP tools are loaded.

### A3: Run from source

If you want to develop or customize the server:

```bash
# 1. Clone the repository
git clone https://github.com/pantalytics/odoo-mcp-pro.git
cd odoo-mcp-pro

# 2. Create a Python virtual environment (requires Python 3.10+)
#    If you don't have uv installed: pip install uv
uv venv --python 3.10
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# 3. Install the package with development dependencies
uv pip install -e ".[dev]"

# 4. Create your config file
cp .env.example .env

# 5. Edit .env — fill in these four values:
#    ODOO_URL=http://localhost:8069
#    ODOO_DB=your_database
#    ODOO_API_KEY=your_api_key
#    ODOO_API_VERSION=json2

# 6. Run the server (stdio mode)
python -m mcp_server_odoo

# 7. Run the tests to make sure everything works
pytest tests/ -x -q
```

---

## 3. Option B: Cloud Deployment on Hetzner VPS

This deploys odoo-mcp-pro as a cloud service so **multiple users** can connect
via Claude.ai with OAuth 2.1 login. Here's what the architecture looks like:

```
Claude.ai / Claude Code
    │  HTTPS + OAuth 2.1 Bearer token
    ▼
Hetzner VPS (€4.50/month)
    ├── Caddy         → automatic HTTPS (Let's Encrypt)
    │   ├── mcp.example.com/production/  → MCP container
    │   └── auth.example.com             → Zitadel
    ├── MCP Server    → connects to your Odoo via API key
    ├── Zitadel       → OAuth 2.1 provider (login/token management)
    └── PostgreSQL    → Zitadel's database
```

**Follow every step in order.** Don't skip ahead.

---

### 3.1 Create a Hetzner Account

1. Go to https://console.hetzner.cloud
2. Click **Register** and create an account
3. Verify your email address
4. Add a payment method (credit card or PayPal)
5. Create a new **Project** — click the **+ New Project** button, name it `MCP Server`

### 3.2 Create an SSH Key

You need an SSH key to log into your server. If you already have one, skip to step 3.

**On your local machine** (not the server):

```bash
# 1. Check if you already have an SSH key
ls -la ~/.ssh/id_ed25519.pub 2>/dev/null || ls -la ~/.ssh/id_rsa.pub 2>/dev/null
```

If that shows a file, you already have a key — skip to step 3.

```bash
# 2. Generate a new key (press Enter to accept defaults)
ssh-keygen -t ed25519 -C "your-email@example.com"
```

```bash
# 3. Copy your public key to clipboard
#    macOS:
cat ~/.ssh/id_ed25519.pub | pbcopy
#    Linux:
cat ~/.ssh/id_ed25519.pub | xclip -selection clipboard
#    Windows (Git Bash):
cat ~/.ssh/id_ed25519.pub | clip
```

4. In Hetzner Console: go to **Security > SSH Keys > Add SSH Key**
5. Paste your public key and give it a name (e.g., "My Laptop")

### 3.3 Provision a VPS

1. In your Hetzner project, click **Add Server**
2. Configure:
   - **Location**: Pick the closest to your Odoo server (e.g., `Falkenstein` or `Helsinki` for Europe)
   - **Image**: `Ubuntu 24.04`
   - **Type**: `Shared vCPU` > `CX22` (2 vCPU, 4 GB RAM, 40 GB disk)
   - **Networking**: Leave defaults (Public IPv4 + IPv6)
   - **SSH Keys**: Check your key from the previous step
   - **Name**: `mcp-server`
3. Click **Create & Buy Now**
4. **Write down the IP address** shown on the server page (e.g., `65.108.xxx.xxx`)

**Cost**: ~€4.50/month. You can delete the server anytime to stop billing.

### 3.4 First Login & Server Setup

Open a terminal on your local machine:

```bash
# SSH into the server (replace with your actual IP)
ssh root@65.108.xxx.xxx
```

> **First time?** Type `yes` when asked about the fingerprint.

Now run these commands **on the server**:

```bash
# ============================================
# STEP 1: Update the operating system
# ============================================
apt update && apt upgrade -y

# ============================================
# STEP 2: Install Docker
# ============================================
curl -fsSL https://get.docker.com | sh

# Verify Docker is installed
docker --version
# Expected output: Docker version 27.x.x or higher

# ============================================
# STEP 3: Install Docker Compose plugin
# ============================================
apt install -y docker-compose-plugin

# Verify Docker Compose is installed
docker compose version
# Expected output: Docker Compose version v2.x.x

# ============================================
# STEP 4: Install Git and Python tools
# ============================================
apt install -y git python3 python3-pip python3-yaml

# Verify Git
git --version
# Expected output: git version 2.x.x

# ============================================
# STEP 5: Install PyYAML (needed by generate.py)
# ============================================
pip3 install pyyaml --break-system-packages
```

> **Why `--break-system-packages`?** Ubuntu 24.04 protects system Python by
> default. This is safe here — we only install one small package on a dedicated
> server.

### 3.5 Set Up DNS

You need two subdomains pointing to your Hetzner server. Go to your domain
registrar's DNS settings (e.g., Cloudflare, Namecheap, TransIP, etc.).

Add these **A records**:

| Type | Name | Value (IP) | TTL |
|------|------|------------|-----|
| A | `mcp` | `65.108.xxx.xxx` | 300 |
| A | `auth` | `65.108.xxx.xxx` | 300 |

> **Cloudflare users**: Set the proxy status to **DNS only** (grey cloud), not
> Proxied (orange cloud). Caddy needs direct access for Let's Encrypt.

**Replace** `65.108.xxx.xxx` with your actual server IP.

Wait 1-5 minutes, then verify **from your server**:

```bash
# Check that DNS resolves to your IP
dig +short mcp.example.com
dig +short auth.example.com
```

Both commands should print your server's IP address. If they show nothing or a
different IP, wait a few more minutes or check your DNS settings.

### 3.6 Clone the Repository

Still on the server:

```bash
# Clone into /opt (standard location for self-hosted services)
cd /opt
git clone https://github.com/pantalytics/odoo-mcp-pro.git

# Verify
ls /opt/odoo-mcp-pro/
# You should see: Dockerfile  README.md  deploy/  mcp_server_odoo/  ...
```

### 3.7 Configure Your Odoo Instance(s)

```bash
cd /opt/odoo-mcp-pro/deploy

# Copy the example config
cp instances.example.yml instances.yml

# Edit it with nano (or vim if you prefer)
nano instances.yml
```

Replace the entire file with your values. Here is a **minimal working example**:

```yaml
# /opt/odoo-mcp-pro/deploy/instances.yml

domain: mcp.example.com

zitadel:
  domain: auth.example.com

oauth:
  issuer_url: https://auth.example.com
  introspection_url: https://auth.example.com/oauth/v2/introspect
  resource_server_url: https://mcp.example.com

instances:
  production:
    odoo_url: https://your-company.odoo.com
    odoo_db: your-company-main-12345678
    odoo_api_key: "${ODOO_API_KEY}"
    api_version: json2
```

**What to replace:**

| Placeholder | Replace with | Example |
|-------------|-------------|---------|
| `mcp.example.com` | Your MCP subdomain | `mcp.pantalytics.com` |
| `auth.example.com` | Your auth subdomain | `auth.pantalytics.com` |
| `https://your-company.odoo.com` | Your Odoo instance URL | `https://pantalytics.odoo.com` |
| `your-company-main-12345678` | Your Odoo database name | `pantalytics-main-4829371` |

> **Odoo.sh database name**: Find it at `https://your-company.odoo.com/web/database/manager`
> or in Odoo.sh dashboard under your branch name.

> **Multiple Odoo instances?** Add more entries under `instances:`:
> ```yaml
> instances:
>   production:
>     odoo_url: https://prod.example.com
>     odoo_db: prod-db
>     odoo_api_key: "${ODOO_API_KEY}"
>     api_version: json2
>   staging:
>     odoo_url: https://staging.example.com
>     odoo_db: staging-db
>     odoo_api_key: "${STAGING_ODOO_API_KEY}"
>     api_version: json2
> ```
> Each instance gets its own URL: `.../production/`, `.../staging/`, etc.

Save and close the file (in nano: `Ctrl+O`, `Enter`, `Ctrl+X`).

### 3.8 Generate Deployment Files

```bash
cd /opt/odoo-mcp-pro/deploy
python3 generate.py
```

**Expected output:**

```
Generated deploy/generated/docker-compose.yml
Generated deploy/generated/Caddyfile

Zitadel:  https://auth.example.com
Instances:
  https://mcp.example.com/production/

To deploy:
  cd deploy/generated
  ...
```

Verify the generated files exist:

```bash
ls -la generated/
# Should show: Caddyfile  docker-compose.yml  machinekey/  zitadel-ready.yaml
```

### 3.9 Create Secrets

Generate all the secrets and save them to an `.env` file:

```bash
cd /opt/odoo-mcp-pro/deploy/generated

# Generate random secrets
MASTERKEY=$(openssl rand -base64 24)
DB_PASSWORD=$(openssl rand -base64 16)

# Create the .env file
cat > .env << EOF
# === Zitadel secrets (auto-generated) ===
ZITADEL_MASTERKEY=${MASTERKEY}
ZITADEL_DB_PASSWORD=${DB_PASSWORD}

# === Your Odoo API key ===
# Replace with your actual API key (see Section 4)
ODOO_API_KEY=your_odoo_api_key_here

# === OAuth credentials (filled in after Phase 2) ===
ZITADEL_CLIENT_ID=placeholder
ZITADEL_CLIENT_SECRET=placeholder
EOF

echo "Created .env file"
```

Now edit the `.env` file to fill in your **Odoo API key**:

```bash
nano .env
```

Replace `your_odoo_api_key_here` with your actual Odoo API key. Save and close.

> **IMPORTANT**: Keep a backup of this `.env` file. The `ZITADEL_MASTERKEY` is
> needed every time you restart the stack. If you lose it, you'll need to
> recreate the entire Zitadel database.

### 3.10 Deploy Phase 1 — Start Zitadel + Caddy

We start in phases because the MCP servers depend on Zitadel being ready first.

```bash
cd /opt/odoo-mcp-pro/deploy/generated

# Start only the database, Zitadel, and Caddy
docker compose up -d zitadel-db zitadel caddy
```

**Wait for Zitadel to initialize** (first boot takes 60-120 seconds):

```bash
# Follow the Zitadel logs
docker compose logs -f zitadel
```

Wait until you see a line like:
```
zitadel  | ... level=info msg="server is listening" ...
```

Then press `Ctrl+C` to stop following logs.

**Verify Zitadel is accessible:**

```bash
# From the server itself
curl -sf https://auth.example.com/debug/ready && echo "OK" || echo "NOT READY"
```

If it says `OK`, proceed. If not:
- Wait another 30 seconds and try again
- Check `docker compose logs zitadel` for errors
- Verify DNS is correct: `dig +short auth.example.com`

**Verify Caddy got TLS certificates:**

```bash
docker compose logs caddy | grep "certificate obtained"
```

You should see successful certificate messages for both domains.

> **Phase 1 checkpoint:**
> - `curl -sf https://auth.example.com/debug/ready` returns OK
> - `docker compose ps` shows zitadel-db, zitadel, and caddy as `Up`
> - Zitadel login page loads at `https://auth.example.com`

### 3.11 Deploy Phase 2 — Configure OAuth

Run the automated setup script. It creates the OAuth applications in Zitadel:

```bash
cd /opt/odoo-mcp-pro/deploy

# Run the setup script (use your actual auth domain)
bash setup-zitadel.sh https://auth.example.com
```

**Expected output:**

```
=== Zitadel MCP OAuth Setup ===
  Zitadel URL: https://auth.example.com

Using admin PAT from ./machinekey/admin.pat
Waiting for Zitadel...
Zitadel is ready!

Creating project 'MCP Server'...
  Project ID: 283948572938457

Creating OIDC application 'mcp-client'...
  Client ID: 283948572938458@mcp-server

Creating API application 'mcp-introspector'...
  Introspection Client ID: 283948572938459@mcp-server

=== Setup Complete ===

Set these env vars on your host before starting MCP servers:

  export ZITADEL_CLIENT_ID=283948572938459@mcp-server
  export ZITADEL_CLIENT_SECRET=abc123xyz789...

Zitadel UI:      https://auth.example.com
MCP Client ID:   283948572938458@mcp-server  (configure in Claude.ai as OIDC client)
Project ID:      283948572938457
```

**Now update the `.env` file with the real credentials:**

```bash
cd /opt/odoo-mcp-pro/deploy/generated
nano .env
```

Replace the placeholder lines with the values from the script output:

```
ZITADEL_CLIENT_ID=283948572938459@mcp-server
ZITADEL_CLIENT_SECRET=abc123xyz789...
```

Save and close.

> **Phase 2 checkpoint:**
> - The setup script completed without errors
> - `.env` contains real `ZITADEL_CLIENT_ID` and `ZITADEL_CLIENT_SECRET` values
> - `curl -sf https://auth.example.com/oauth/v2/introspect --user "${ZITADEL_CLIENT_ID}:${ZITADEL_CLIENT_SECRET}" -d "token=test"` returns `{"active":false}` (not an error)

> **Troubleshooting**: If the script says "admin.pat not found", make sure:
> 1. The `machinekey/` directory exists in `deploy/generated/`
> 2. Zitadel has fully started (check logs)
> 3. The PAT file was created: `ls -la generated/machinekey/admin.pat`

### 3.12 Deploy Phase 3 — Start MCP Servers

Now start all services (including the MCP server containers):

```bash
cd /opt/odoo-mcp-pro/deploy/generated

# Restart everything to pick up the new OAuth credentials
docker compose down
docker compose up -d --build
```

> **Why `--build`?** This builds the MCP server Docker image from the local
> source code. On subsequent deploys after code changes, always include `--build`.

> **Phase 3 checkpoint:**
> - `docker compose ps` shows all containers as `Up`
> - `curl -s -o /dev/null -w "%{http_code}" https://mcp.example.com/production/mcp/` returns `401` (OAuth protecting — correct!)

### 3.13 Verify Everything Works

**Check all containers are running:**

```bash
cd /opt/odoo-mcp-pro/deploy/generated
docker compose ps
```

Expected output (all should be `Up` or `healthy`):

```
NAME              IMAGE                             STATUS
zitadel-db        postgres:16-alpine                Up (healthy)
zitadel           ghcr.io/zitadel/zitadel:latest    Up (healthy)
mcp-production    deploy-generated-mcp-production   Up
mcp-caddy         caddy:2-alpine                    Up
```

If any container shows `Restarting` or `Exit`, check its logs:

```bash
docker compose logs <container-name>
```

**Test the MCP server directly:**

```bash
# Should return the MCP server info or a 401 (which means it's running but needs auth)
curl -s -o /dev/null -w "%{http_code}" https://mcp.example.com/production/mcp/
# Expected: 401 (OAuth is protecting it — that's correct!)
```

**Test Zitadel UI:**

Open `https://auth.example.com` in your browser. You should see the Zitadel
login page. The default admin credentials are:
- **Username**: `admin@auth.example.com` (replace with your domain)
- **Password**: `Password1!` (change this immediately after first login!)

### 3.14 Connect from Claude.ai

1. Open https://claude.ai
2. Go to **Settings** > **Integrations**
3. Click **Add Integration** or **Add MCP Server**
4. Enter:
   - **Name**: `My Odoo (Production)` (any name you like)
   - **URL**: `https://mcp.example.com/production/`
5. Click **Connect** — you'll be redirected to Zitadel for OAuth login
6. Log in with your Zitadel user credentials
7. Authorize the application
8. You're connected!

**Test it** — ask Claude:

> "Search for the top 5 contacts in Odoo"

You should see Claude calling the `search_records` tool and returning results
from your Odoo instance.

---

## 4. Option C: Cloud with Zitadel Cloud (simpler)

Instead of self-hosting Zitadel (Option B), you can use **Zitadel Cloud** — a managed
SaaS that eliminates the need for PostgreSQL, container management, and setup scripts.

**Trade-off**: Slightly less control, but much simpler to set up and maintain.

### 4.1 Create a Zitadel Cloud Account

1. Go to https://zitadel.cloud and sign up
2. Create a new instance (e.g., `odoo-mcp-pro`)
3. Note your instance URL (e.g., `https://odoo-mcp-pro-x4tprs.us1.zitadel.cloud`)

### 4.2 Create OAuth Applications

In Zitadel Cloud console, create two applications in a project:

**App 1: OIDC Web Application** (for Claude.ai login)
- Type: **Web** (OIDC)
- Auth method: **PKCE** (no client secret — Claude.ai is a public client)
- Redirect URI: `https://claude.ai/api/mcp/auth_callback`
- Note the **Client ID**

**App 2: API Application** (for token introspection)
- Type: **API**
- Auth method: **Basic** (client_id + client_secret)
- Note the **Client ID** and **Client Secret**

### 4.3 Configure instances.yml

```yaml
domain: mcp.example.com

# No 'zitadel:' section — using Zitadel Cloud instead of self-hosted

oauth:
  issuer_url: https://your-instance.zitadel.cloud
  introspection_url: https://your-instance.zitadel.cloud/oauth/v2/introspect
  resource_server_url: https://mcp.example.com

instances:
  production:
    odoo_url: https://your-company.odoo.com
    odoo_db: ""                            # empty for Odoo.sh
    odoo_api_key: "${ODOO_API_KEY}"
    api_version: json2
    yolo: "true"                           # JSON/2 delegates ACLs to Odoo
```

### 4.4 Deploy

```bash
cd deploy
python3 generate.py
cd generated

# Create .env with secrets
cat > .env << EOF
ODOO_API_KEY=your_odoo_api_key
ZITADEL_CLIENT_ID=your_api_app_client_id
ZITADEL_CLIENT_SECRET=your_api_app_client_secret
EOF

# No Zitadel to start — just MCP + Caddy
docker compose up -d --build
```

Since there's no self-hosted Zitadel, the stack is lighter: just MCP container(s) + Caddy.

---

## 5. Microsoft Entra ID Federation (optional)

Allow users to log in with their Microsoft work accounts. Zitadel handles the
federation — the MCP server needs **no code changes**.

### 5.1 Azure Entra ID — Create App Registration

1. Go to https://entra.microsoft.com → **App registrations** → **New registration**
2. Name: `Zitadel SSO` (or any name)
3. Supported account types: **Accounts in this organizational directory only**
4. Redirect URI:
   - Platform: **Web**
   - URI: `https://<your-zitadel-instance>/idps/callback`
   - Example: `https://odoo-mcp-pro-x4tprs.us1.zitadel.cloud/idps/callback`
   - Note: older/self-hosted Zitadel may use `/ui/login/login/externalidp/callback` instead
5. Click **Register**

After registration:
1. Note the **Application (client) ID** and **Directory (tenant) ID**
2. Go to **Certificates & secrets** → **New client secret** → copy the **Value**
3. Go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated**:
   - `openid`
   - `profile`
   - `email`

### 5.2 Zitadel — Add Microsoft IDP

1. In Zitadel console: **Settings** → **Identity Providers** → **New**
2. Choose the **Microsoft / Azure AD** template
3. Fill in:
   - **Client ID**: the Application (client) ID from Azure
   - **Client Secret**: the secret Value from Azure
   - **Tenant**: the Directory (tenant) ID from Azure
4. Scopes: `openid profile email`
5. Save

### 5.3 Enable in Login Settings

1. Go to **Settings** → **Login Settings**
2. Enable the Microsoft IDP
3. Optionally configure:
   - **Auto-linking**: Link to existing Zitadel users by email
   - **Auto-creation**: Create Zitadel users on first Microsoft login

### 5.4 Test

1. Open Claude.ai in an **incognito window**
2. Connect to your MCP server
3. The Zitadel login page should show a **"Sign in with Microsoft"** button
4. Log in with your Microsoft work account
5. Verify MCP tools work

---

## 6. How to Generate an Odoo API Key

The MCP server uses an API key to authenticate with Odoo on behalf of a user.
The key has the same permissions as the user it belongs to.

### Via the Odoo Web UI (recommended)

1. Log in to your Odoo instance as the user you want the MCP server to use
   (typically an admin or a dedicated service user)
2. Click your **avatar** (top right) > **My Profile** (or **Preferences**)
3. Scroll down to **Account Security**
4. Click **New API Key**
5. Enter a description: `MCP Server`
6. Click **Generate Key**
7. **Copy the key immediately** — it's shown only once!

> **Odoo.sh users**: The process is identical. Log into your Odoo.sh instance's
> web interface and follow the same steps.

> **Security tip**: Create a dedicated Odoo user (e.g., `mcp-service@yourcompany.com`)
> with only the permissions the MCP server needs, rather than using your admin account.

### Finding your database name

- **Odoo.sh**: Check your branch name in the Odoo.sh dashboard (e.g., `mycompany-main-4829371`)
- **Self-hosted**: Go to `https://your-odoo.com/web/database/manager`
- **Single database**: If you only have one database, it may auto-detect — but
  it's safer to set it explicitly

---

## 7. Day-to-Day Operations

### View logs

```bash
cd /opt/odoo-mcp-pro/deploy/generated

# All services
docker compose logs -f --tail=50

# Specific service
docker compose logs -f mcp-production
docker compose logs -f zitadel
docker compose logs -f caddy
```

### Restart the stack

```bash
cd /opt/odoo-mcp-pro/deploy/generated
docker compose restart
```

### Stop everything

```bash
cd /opt/odoo-mcp-pro/deploy/generated
docker compose down
```

### Start again

```bash
cd /opt/odoo-mcp-pro/deploy/generated
docker compose up -d
```

### Update to a new version

When a new version of odoo-mcp-pro is released:

```bash
# 1. Go to the repository
cd /opt/odoo-mcp-pro

# 2. Pull latest changes
git pull origin main

# 3. Regenerate deployment files (in case they changed)
cd deploy
python3 generate.py

# 4. Rebuild and restart the MCP containers
cd generated
docker compose up -d --build

# 5. Verify
docker compose ps
docker compose logs -f mcp-production --tail=20
```

### Add a new Odoo instance

1. Edit `instances.yml` to add the new instance
2. Set the new API key as an env var in `.env`
3. Run `python3 generate.py`
4. Run `docker compose up -d --build` in `generated/`
5. The new instance is available at `https://mcp.example.com/<instance-name>/`

### Add users to Zitadel

1. Go to `https://auth.example.com`
2. Log in as admin
3. Go to **Users** > **+ New**
4. Fill in email, name, password
5. The user can now log in when connecting via Claude.ai

---

## 8. Troubleshooting

### "Connection refused" when Claude tries to connect

**Cause**: The MCP server container is not running or not reachable.

```bash
# Check if the container is running
docker compose ps

# Check its logs
docker compose logs mcp-production

# Common causes:
# 1. Container crashed → check logs for Python tracebacks
# 2. Wrong ODOO_URL → must be reachable from inside the container
# 3. Firewall blocking ports 80/443
```

### Zitadel won't start or keeps restarting

```bash
# Check Zitadel logs
docker compose logs zitadel --tail=50

# Check if the database is healthy
docker compose ps zitadel-db
docker compose logs zitadel-db

# Common causes:
# 1. ZITADEL_MASTERKEY not set or too short
echo $ZITADEL_MASTERKEY | wc -c   # Must be > 32 characters

# 2. Database not ready yet → wait and retry
docker compose restart zitadel

# 3. Corrupted first init → nuclear option: reset everything
docker compose down -v   # WARNING: deletes all Zitadel data!
docker compose up -d zitadel-db zitadel caddy
# Then re-run setup-zitadel.sh
```

### Caddy can't get TLS certificates

```bash
# Check Caddy logs
docker compose logs caddy

# Common causes:
# 1. DNS not pointing to this server
dig +short mcp.example.com    # Must show your server IP
dig +short auth.example.com   # Must show your server IP

# 2. Ports 80/443 blocked by firewall
# Hetzner has no firewall by default, but if you added one:
# Go to Hetzner Console > Networking > Firewalls
# Add rules: TCP 80 (HTTP) and TCP 443 (HTTPS) from 0.0.0.0/0

# 3. Cloudflare proxy enabled (must be DNS-only / grey cloud)
```

### MCP server can't connect to Odoo

```bash
# Check MCP container logs
docker compose logs mcp-production

# Test Odoo connectivity from inside the container
docker compose exec mcp-production python -c "
import httpx
try:
    r = httpx.get('https://your-odoo.com/web/health', timeout=10)
    print(f'Status: {r.status_code}')
    print(f'Body: {r.text[:200]}')
except Exception as e:
    print(f'Error: {e}')
"

# Common causes:
# 1. Wrong ODOO_URL in instances.yml
# 2. Odoo is down or unreachable from the server
# 3. Wrong API key → test it manually:
docker compose exec mcp-production python -c "
import httpx
r = httpx.post(
    'https://your-odoo.com/json/2/res.partner/search_count',
    headers={
        'Authorization': 'Bearer YOUR_API_KEY',
        'X-Odoo-Database': 'your-database',
        'Content-Type': 'application/json',
    },
    json={},
    timeout=10,
)
print(f'Status: {r.status_code}')
print(f'Body: {r.text[:200]}')
"
```

### OAuth "invalid_token" or "unauthorized" errors

```bash
# 1. Verify credentials are set
cd /opt/odoo-mcp-pro/deploy/generated
grep ZITADEL_CLIENT .env

# 2. Test token introspection endpoint
source .env
curl -s https://auth.example.com/oauth/v2/introspect \
  --user "${ZITADEL_CLIENT_ID}:${ZITADEL_CLIENT_SECRET}" \
  -d "token=fake-token-for-testing"
# Expected: {"active":false} (not an error)

# 3. If you get a connection error: Zitadel might not be accessible
# from inside the Docker network. Check:
docker compose exec mcp-production python -c "
import httpx
r = httpx.get('http://zitadel:8080/debug/ready', timeout=5)
print(r.status_code, r.text)
"
```

### setup-zitadel.sh says "admin.pat not found"

```bash
# The PAT file is created by Zitadel on first boot
# Check if it exists:
ls -la /opt/odoo-mcp-pro/deploy/generated/machinekey/admin.pat

# If not:
# 1. Zitadel might not have finished initializing
docker compose logs zitadel | grep "PAT"

# 2. The machinekey volume might not be mounted correctly
docker compose exec zitadel ls -la /machinekey/

# 3. Restart Zitadel and wait for full init
docker compose restart zitadel
sleep 60
ls -la /opt/odoo-mcp-pro/deploy/generated/machinekey/admin.pat
```

### How to start completely fresh

If everything is broken and you want to start over:

```bash
cd /opt/odoo-mcp-pro/deploy/generated

# Stop and remove all containers AND volumes (deletes all data!)
docker compose down -v

# Remove generated files
rm -rf machinekey/admin.pat

# Go back to step 3.9 (Create Secrets) and start from there
```

### Server runs out of disk space

```bash
# Check disk usage
df -h

# Clean up Docker (removes unused images, containers, volumes)
docker system prune -a --volumes

# Check what's using space
du -sh /var/lib/docker/*
```
