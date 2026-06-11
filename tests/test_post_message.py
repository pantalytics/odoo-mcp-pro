"""Tests for the post_message chatter tool."""

from __future__ import annotations

import os
from unittest.mock import Mock

import pytest

from mcp_server_odoo.access_control import AccessControlError
from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.tools import OdooToolHandler

# ---------------------------------------------------------------------------
# Unit tests with a mocked OdooConnection
# ---------------------------------------------------------------------------


class TestPostMessageUnit:
    """Mock-driven tests for post_message argument assembly + return shape."""

    @pytest.fixture
    def mock_app(self):
        app = Mock()
        app.tool = Mock(side_effect=lambda **kwargs: lambda func: func)
        return app

    @pytest.fixture
    def mock_connection(self):
        conn = Mock()
        conn.is_authenticated = True
        conn._base_url = "http://localhost:8169"
        return conn

    @pytest.fixture
    def mock_access_controller(self):
        controller = Mock()
        controller.validate_model_access = Mock()
        return controller

    @pytest.fixture
    def mock_config(self):
        config = Mock()
        config.url = "http://localhost:8169"
        return config

    @pytest.fixture
    def handler(self, mock_app, mock_connection, mock_access_controller, mock_config):
        return OdooToolHandler(mock_app, mock_connection, mock_access_controller, mock_config)

    @pytest.mark.asyncio
    async def test_message_post_kwargs_minimal(self, handler, mock_connection):
        """Default subtype is mt_comment; only body is required in kwargs."""
        # existence check + message read + outlook field probe
        mock_connection.read.side_effect = [
            [{"id": 7}],  # existence check
            [{"subtype_id": [1, "Discussions"], "attachment_ids": []}],  # message readback
        ]
        mock_connection.fields_get.return_value = {}  # no outlook field
        mock_connection.call_method.return_value = 42  # message_post returns mail.message id
        mock_connection.search_read.return_value = []  # no notifications

        result = await handler._handle_post_message_tool(
            model="res.partner", record_id=7, body="<p>hi</p>"
        )

        assert result["success"] is True
        assert result["message_id"] == 42
        assert result["subtype"] == "Discussions"
        assert result["outlook_pro_message_id"] is None
        assert result["notifications"] == []

        # call_method signature: (model, method, ids=[record_id], **kwargs)
        positional = mock_connection.call_method.call_args.args
        kw = mock_connection.call_method.call_args.kwargs
        assert positional == ("res.partner", "message_post")
        assert kw["ids"] == [7]
        assert kw["body"] == "<p>hi</p>"
        # body_is_html must always be True over RPC, otherwise Odoo HTML-escapes
        # the body and chatter shows literal "&lt;p&gt;..." text.
        assert kw["body_is_html"] is True
        assert kw["subtype_xmlid"] == "mail.mt_comment"
        assert kw["message_type"] == "comment"
        assert "subject" not in kw
        assert "partner_ids" not in kw
        assert "outgoing_email_to" not in kw

    @pytest.mark.asyncio
    async def test_message_post_kwargs_full(self, handler, mock_connection):
        """All optional kwargs forwarded; cc maps to outgoing_email_to."""
        mock_connection.read.side_effect = [
            [{"id": 7}],
            [{"subtype_id": [2, "Note"], "attachment_ids": [9, 10]}],
        ]
        mock_connection.fields_get.return_value = {}
        mock_connection.call_method.return_value = 99
        mock_connection.search_read.return_value = []

        await handler._handle_post_message_tool(
            model="crm.lead",
            record_id=7,
            body="<p>x</p>",
            subject="Subject",
            partner_ids=[1, 2],
            attachment_ids=[9, 10],
            subtype_xmlid="mail.mt_note",
            cc="extra@example.com",
        )

        kw = mock_connection.call_method.call_args.kwargs
        assert kw["ids"] == [7]
        assert kw["subject"] == "Subject"
        assert kw["partner_ids"] == [1, 2]
        assert kw["attachment_ids"] == [9, 10]
        assert kw["subtype_xmlid"] == "mail.mt_note"
        assert kw["outgoing_email_to"] == "extra@example.com"

    @pytest.mark.asyncio
    async def test_message_post_returns_list_normalised(self, handler, mock_connection):
        """Some transports wrap singleton id in a list — we unwrap."""
        mock_connection.read.side_effect = [
            [{"id": 7}],
            [{"subtype_id": [1, "Discussions"], "attachment_ids": []}],
        ]
        mock_connection.fields_get.return_value = {}
        mock_connection.call_method.return_value = [55]
        mock_connection.search_read.return_value = []

        result = await handler._handle_post_message_tool(
            model="res.partner", record_id=7, body="<p>hi</p>"
        )
        assert result["message_id"] == 55

    @pytest.mark.asyncio
    async def test_outlook_pro_message_id_surfaced(self, handler, mock_connection):
        """When mail.message has x_microsoft_message_id, surface it."""
        mock_connection.read.side_effect = [
            [{"id": 7}],
            [
                {
                    "subtype_id": [1, "Discussions"],
                    "attachment_ids": [],
                    "x_microsoft_message_id": "<AAMkAG...>",
                }
            ],
        ]
        mock_connection.fields_get.return_value = {"x_microsoft_message_id": {"type": "char"}}
        mock_connection.call_method.return_value = 42
        mock_connection.search_read.return_value = []

        result = await handler._handle_post_message_tool(
            model="res.partner", record_id=7, body="<p>hi</p>"
        )
        assert result["outlook_pro_message_id"] == "<AAMkAG...>"

    @pytest.mark.asyncio
    async def test_record_not_found(self, handler, mock_connection):
        mock_connection.read.return_value = []
        with pytest.raises(ValidationError, match="Record not found"):
            await handler._handle_post_message_tool(
                model="res.partner", record_id=999, body="<p>x</p>"
            )

    @pytest.mark.asyncio
    async def test_empty_body_rejected(self, handler):
        with pytest.raises(ValidationError, match="body is required"):
            await handler._handle_post_message_tool(model="res.partner", record_id=7, body="")

    @pytest.mark.asyncio
    async def test_access_denied(self, handler, mock_access_controller):
        mock_access_controller.validate_model_access.side_effect = AccessControlError(
            "Access denied to res.partner.write"
        )
        with pytest.raises(ValidationError, match="Access denied"):
            await handler._handle_post_message_tool(
                model="res.partner", record_id=7, body="<p>x</p>"
            )

    @pytest.mark.asyncio
    async def test_notifications_formatted(self, handler, mock_connection):
        """Each mail.notification row is reduced to a flat per-recipient dict."""
        mock_connection.read.side_effect = [
            [{"id": 7}],
            [{"subtype_id": [1, "Discussions"], "attachment_ids": []}],
        ]
        mock_connection.fields_get.return_value = {}
        mock_connection.call_method.return_value = 42
        mock_connection.search_read.return_value = [
            {
                "res_partner_id": [11, "Alice"],
                "notification_type": "email",
                "notification_status": "sent",
                "failure_reason": False,
            },
            {
                "res_partner_id": [12, "Bob"],
                "notification_type": "inbox",
                "notification_status": "ready",
                "failure_reason": False,
            },
        ]

        result = await handler._handle_post_message_tool(
            model="res.partner",
            record_id=7,
            body="<p>hi</p>",
            partner_ids=[11, 12],
        )
        assert len(result["notifications"]) == 2
        assert result["notifications"][0]["partner_name"] == "Alice"
        assert result["notifications"][0]["type"] == "email"
        assert result["notifications"][0]["status"] == "sent"
        assert result["notifications"][1]["partner_name"] == "Bob"
        assert result["notifications"][1]["type"] == "inbox"
        assert result["degraded_details"] == []


# ---------------------------------------------------------------------------
# Integration tests against the local docker stack
# ---------------------------------------------------------------------------


# Read deploy/.env.test if available so the test can target odoo18 / odoo19
# without needing the user to mess with environment variables.
def _load_local_test_env() -> dict:
    """Best-effort load of admin-repo's deploy/.env.test for local testing."""
    candidates = [
        os.path.join(
            os.path.dirname(__file__), "..", "..", "odoo-mcp-pro-admin", "deploy", ".env.test"
        ),
    ]
    for path in candidates:
        if os.path.exists(path):
            out = {}
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    out[k] = v
            return out
    return {}


LOCAL_ENV = _load_local_test_env()


def _make_handler_for(target: str):
    """Build a tool handler against odoo18 (vanilla) or odoo19 (outlook pro).

    Returns (handler, base_url, db) or None if the env / server is unreachable.
    """
    import socket

    from mcp.server.fastmcp import FastMCP

    from mcp_server_odoo.access_control import AccessController
    from mcp_server_odoo.config import OdooConfig
    from mcp_server_odoo.odoo_connection import OdooConnection

    if target == "odoo18":
        url = LOCAL_ENV.get("ODOO18_URL")
        db = LOCAL_ENV.get("ODOO18_DB")
        key = LOCAL_ENV.get("ODOO18_API_KEY")
    elif target == "odoo19":
        url = LOCAL_ENV.get("ODOO19_URL")
        db = LOCAL_ENV.get("ODOO19_DB")
        key = LOCAL_ENV.get("ODOO19_API_KEY")
    else:
        return None

    if not url or not key or not db:
        return None

    # Quick TCP probe
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8069
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    if sock.connect_ex((host, port)) != 0:
        sock.close()
        return None
    sock.close()

    config = OdooConfig(url=url, database=db, api_key=key, username="admin", api_version="xmlrpc")
    conn = OdooConnection(config)
    conn.connect()
    conn.authenticate()
    app = FastMCP("test")
    handler = OdooToolHandler(app, conn, AccessController(config), config)
    return handler, url, db


def _ensure_test_partner(conn, name: str, email: str) -> int:
    ids = conn.search("res.partner", [("name", "=", name)], limit=1)
    if ids:
        return ids[0]
    return conn.create("res.partner", {"name": name, "email": email})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_note_silent_on_odoo18():
    """A pure note (mt_note, no recipients) creates 0 notifications, 0 mail.mail."""
    bundle = _make_handler_for("odoo18")
    if bundle is None:
        pytest.skip("odoo18 not reachable or .env.test missing")
    handler, _, _ = bundle
    partner_id = _ensure_test_partner(handler.connection, "post_message_test", "test@example.com")

    result = await handler._handle_post_message_tool(
        model="res.partner",
        record_id=partner_id,
        body="<p>integration note</p>",
        subtype_xmlid="mail.mt_note",
    )
    assert result["success"] is True
    assert result["subtype"] == "Note"
    assert result["notifications"] == []
    assert result["outlook_pro_message_id"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_message_with_partner_on_odoo18():
    """Message with explicit recipient creates a notification."""
    bundle = _make_handler_for("odoo18")
    if bundle is None:
        pytest.skip("odoo18 not reachable or .env.test missing")
    handler, _, _ = bundle
    partner_id = _ensure_test_partner(handler.connection, "post_message_test", "test@example.com")
    rcpt_id = _ensure_test_partner(handler.connection, "post_message_rcpt", "rcpt@example.com")

    result = await handler._handle_post_message_tool(
        model="res.partner",
        record_id=partner_id,
        body="<p>integration message</p>",
        partner_ids=[rcpt_id],
    )
    assert result["success"] is True
    assert result["subtype"] == "Discussions"
    assert any(n["partner_id"] == rcpt_id for n in result["notifications"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_outlook_pro_field_present_on_odoo19():
    """On odoo19+pan_outlook_pro, x_microsoft_message_id field is read (may be None without OAuth)."""
    bundle = _make_handler_for("odoo19")
    if bundle is None:
        pytest.skip("odoo19 not reachable or .env.test missing")
    handler, _, _ = bundle

    # Confirm pan_outlook_pro is installed; skip if not
    installed = handler.connection.search(
        "ir.module.module",
        [("name", "=", "pan_outlook_pro"), ("state", "=", "installed")],
        limit=1,
    )
    if not installed:
        pytest.skip("pan_outlook_pro not installed on odoo19")

    partner_id = _ensure_test_partner(handler.connection, "post_message_test", "test@example.com")
    result = await handler._handle_post_message_tool(
        model="res.partner",
        record_id=partner_id,
        body="<p>outlook pro probe</p>",
        subtype_xmlid="mail.mt_note",  # silent — we only need the field probe
    )
    assert result["success"] is True
    # outlook_pro_message_id key is present (None acceptable for a note)
    assert "outlook_pro_message_id" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_cc_rejected_on_v18():
    """outgoing_email_to is v19+; v18 must surface a clear error."""
    bundle = _make_handler_for("odoo18")
    if bundle is None:
        pytest.skip("odoo18 not reachable or .env.test missing")
    handler, _, _ = bundle
    partner_id = _ensure_test_partner(handler.connection, "post_message_test", "test@example.com")

    with pytest.raises(ValidationError):
        await handler._handle_post_message_tool(
            model="res.partner",
            record_id=partner_id,
            body="<p>x</p>",
            cc="extra@example.com",
        )
