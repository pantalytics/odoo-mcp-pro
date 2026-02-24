FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY mcp_server_odoo/ mcp_server_odoo/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

ARG GIT_COMMIT=unknown

RUN useradd -r -u 1000 -s /usr/sbin/nologin appuser

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"
ENV ODOO_MCP_TRANSPORT=streamable-http
ENV ODOO_MCP_HOST=0.0.0.0
ENV ODOO_MCP_PORT=8000
ENV GIT_COMMIT=${GIT_COMMIT}

EXPOSE 8000

USER appuser
CMD ["python", "-m", "mcp_server_odoo"]
