"""message_post with a cc address (#205).

A cc address that is no contact in Odoo gets a mail.notification with no
res_partner_id, only the address. The result schema required a partner id,
so a mail that went out was reported as a failure, and an AI that trusts the
error sends it again.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from mcp_server_odoo.schemas import PostMessageResult
from mcp_server_odoo.tools import OdooToolHandler


@pytest.fixture
def mock_connection():
    conn = Mock()
    conn.is_authenticated = True
    conn._base_url = "http://localhost:8169"
    conn.read.side_effect = [
        [{"id": 7}],
        [{"subtype_id": [1, "Discussions"], "attachment_ids": []}],
    ]
    conn.fields_get.return_value = {}
    conn.call_method.return_value = 42
    return conn


@pytest.fixture
def handler(mock_connection):
    app = Mock()
    app.tool = Mock(side_effect=lambda **kwargs: lambda func: func)
    access = Mock()
    access.validate_model_access = Mock()
    config = Mock()
    config.url = "http://localhost:8169"
    return OdooToolHandler(app, mock_connection, access, config)


CC_ROWS = [
    {
        "res_partner_id": [11, "Alice"],
        "notification_type": "email",
        "notification_status": "sent",
        "failure_reason": False,
        "mail_email_address": False,
    },
    {
        "res_partner_id": False,
        "notification_type": "email",
        "notification_status": "sent",
        "failure_reason": False,
        "mail_email_address": "colleague@example.com",
    },
]


@pytest.mark.asyncio
async def test_a_cc_address_is_a_row_with_its_address(handler, mock_connection):
    mock_connection.search_read.return_value = CC_ROWS
    result = await handler._handle_post_message_tool(
        model="res.partner",
        record_id=7,
        body="<p>hi</p>",
        partner_ids=[11],
        cc="colleague@example.com",
    )
    validated = PostMessageResult(**result)
    cc_row = validated.notifications[1]
    assert cc_row.partner_id is None
    assert cc_row.email == "colleague@example.com"
    assert cc_row.status == "sent"
    assert validated.notifications[0].partner_id == 11
    assert validated.notifications[0].email is None


@pytest.mark.asyncio
async def test_the_address_column_is_asked_for_only_with_cc(handler, mock_connection):
    """mail_email_address exists from Odoo 19, the only series that takes cc:
    asking for it on an older Odoo would fail the read."""
    mock_connection.search_read.return_value = []
    await handler._handle_post_message_tool(
        model="res.partner", record_id=7, body="<p>hi</p>", partner_ids=[11]
    )
    notif_reads = [
        c for c in mock_connection.search_read.call_args_list if c.args[0] == "mail.notification"
    ]
    assert notif_reads
    assert "mail_email_address" not in notif_reads[-1].args[2]
