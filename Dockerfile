FROM python:3.12-slim

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["python", "-m", "mcp_server_odoo", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
