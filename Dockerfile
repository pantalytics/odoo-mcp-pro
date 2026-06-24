# syntax=docker/dockerfile:1

# ---- builder: install deps into a venv using the locked uv.lock ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install dependencies first (cached layer), without the project itself.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Now add the project source and install it.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- runtime: slim image with just the venv ----
FROM python:3.12-slim-bookworm AS runtime

# Non-root user
RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

# Commit the image was built from; surfaced by the server's health/version output.
ARG GIT_COMMIT=unknown

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GIT_COMMIT=${GIT_COMMIT} \
    ODOO_MCP_TRANSPORT=streamable-http \
    ODOO_MCP_HOST=0.0.0.0 \
    ODOO_MCP_PORT=8000

USER app
EXPOSE 8000

# Streamable-HTTP transport; endpoint is served at /mcp/
CMD ["python", "-m", "mcp_server_odoo", "--transport", "streamable-http"]
