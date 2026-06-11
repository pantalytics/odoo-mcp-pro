"""Degraded-detail tests for post_message (ticket 61): a successful post
must never be reported as a failure when follow-up enrichment reads fail."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.tools import OdooToolHandler


class TestPostMessageDegradedDetail:
    """A successful post must never be reported as a failure (ticket 61).

    Some Odoo builds (seen on Odoo Online, helpdesk.ticket and discuss.channel)
    cannot serialise mail.message-related objects in the RPC response of the
    follow-up reads: the server raises "TypeError: cannot marshal <class 'File'>
    objects" in OdooMarshaller.dumps() AFTER the message already landed in the
    chatter. The tool must then return success with degraded detail, not raise.
    """

    # The fault our XML-RPC client receives when Odoo's OdooMarshaller chokes
    # on the response of a follow-up read (ticket 61, yape.odoo.com).
    MARSHAL_FAULT = Exception(
        '<Fault 1: "Traceback (most recent call last):\\n...\\n'
        "TypeError: cannot marshal <class 'File'> objects\\n\""
        " in OdooMarshaller.dumps()>"
    )

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
    def handler(self, mock_app, mock_connection):
        controller = Mock()
        controller.validate_model_access = Mock()
        config = Mock()
        config.url = "http://localhost:8169"
        return OdooToolHandler(mock_app, mock_connection, controller, config)

    @pytest.mark.asyncio
    async def test_message_readback_failure_degrades(self, handler, mock_connection):
        """mail.message readback fails to serialise -> success with degraded detail."""
        mock_connection.read.side_effect = [
            [{"id": 7}],  # existence check
            self.MARSHAL_FAULT,  # message readback blows up server-side
        ]
        mock_connection.fields_get.return_value = {}
        mock_connection.call_method.return_value = 42
        mock_connection.search_read.return_value = []

        result = await handler._handle_post_message_tool(
            model="helpdesk.ticket", record_id=7, body="<p>hi</p>"
        )

        assert result["success"] is True
        assert result["message_id"] == 42
        assert result["subtype"] is None
        assert result["attachment_count"] == 0
        assert result["outlook_pro_message_id"] is None
        assert result["degraded_details"] == ["message details"]
        assert "message details" in result["message"]
        # The notification read still ran and succeeded
        assert result["notifications"] == []

    @pytest.mark.asyncio
    async def test_notification_read_failure_degrades(self, handler, mock_connection):
        """mail.notification fan-out read fails -> success, notifications empty."""
        mock_connection.read.side_effect = [
            [{"id": 7}],
            [{"subtype_id": [1, "Discussions"], "attachment_ids": []}],
        ]
        mock_connection.fields_get.return_value = {}
        mock_connection.call_method.return_value = 42
        mock_connection.search_read.side_effect = self.MARSHAL_FAULT

        result = await handler._handle_post_message_tool(
            model="discuss.channel", record_id=7, body="<p>hi</p>"
        )

        assert result["success"] is True
        assert result["message_id"] == 42
        assert result["subtype"] == "Discussions"
        assert result["notifications"] == []
        assert result["degraded_details"] == ["notification status"]
        assert "notification status" in result["message"]

    @pytest.mark.asyncio
    async def test_all_followup_reads_fail_still_success(self, handler, mock_connection):
        """Both follow-up reads fail -> still success, both sections degraded."""
        mock_connection.read.side_effect = [
            [{"id": 7}],
            self.MARSHAL_FAULT,
        ]
        mock_connection.fields_get.return_value = {}
        mock_connection.call_method.return_value = 42
        mock_connection.search_read.side_effect = self.MARSHAL_FAULT

        result = await handler._handle_post_message_tool(
            model="helpdesk.ticket", record_id=7, body="<p>hi</p>"
        )

        assert result["success"] is True
        assert result["message_id"] == 42
        assert result["degraded_details"] == ["message details", "notification status"]
        assert "posted mail.message 42" in result["message"]

    @pytest.mark.asyncio
    async def test_followup_failure_logged_loudly(self, handler, mock_connection, caplog):
        """The underlying exception is logged with a traceback, not swallowed."""
        import logging

        mock_connection.read.side_effect = [
            [{"id": 7}],
            self.MARSHAL_FAULT,
        ]
        mock_connection.fields_get.return_value = {}
        mock_connection.call_method.return_value = 42
        mock_connection.search_read.return_value = []

        with caplog.at_level(logging.ERROR, logger="mcp_server_odoo.tools"):
            result = await handler._handle_post_message_tool(
                model="helpdesk.ticket", record_id=7, body="<p>hi</p>"
            )

        assert result["success"] is True
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("posted to helpdesk.ticket:7" in r.getMessage() for r in error_records)
        assert any(r.exc_info and "cannot marshal" in str(r.exc_info[1]) for r in error_records)

    @pytest.mark.asyncio
    async def test_post_itself_failing_still_raises(self, handler, mock_connection):
        """Tolerance only applies AFTER the post; a failing message_post still errors."""
        mock_connection.read.return_value = [{"id": 7}]
        mock_connection.fields_get.return_value = {}
        mock_connection.call_method.side_effect = Exception("boom")

        with pytest.raises(ValidationError, match="Failed to post message"):
            await handler._handle_post_message_tool(
                model="res.partner", record_id=7, body="<p>hi</p>"
            )
