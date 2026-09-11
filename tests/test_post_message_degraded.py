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


class TestPostMessageResultNotEncodable:
    """message_post ran and committed, but Odoo could not encode its return
    value (ticket 219, Scewo: the customer got the email twice after a retry).
    The posted message must be recovered from the chatter, never reported as
    a failure, and never claimed when it cannot be found.
    """

    # The sanitized fault as the tool sees it: the class name is stripped by
    # ErrorSanitizer, "cannot marshal" survives.
    XMLRPC_FAULT = Exception(
        "Operation failed: File, in __dump f = self.dispatch[type(value)] "
        "KeyError: ... TypeError: cannot marshal objects"
    )
    JSON2_FAULT = Exception(
        "Server error (500): Object of type MailMessage is not JSON serializable"
    )

    @pytest.fixture
    def handler(self):
        app = Mock()
        app.tool = Mock(side_effect=lambda **kwargs: lambda func: func)
        controller = Mock()
        controller.validate_model_access = Mock()
        config = Mock()
        config.url = "http://localhost:8169"
        conn = Mock()
        conn.is_authenticated = True
        conn.uid = 5
        conn._base_url = "http://localhost:8169"
        conn.read.side_effect = [
            [{"id": 7}],  # existence check
            [{"subtype_id": [1, "Discussions"], "attachment_ids": []}],
        ]
        conn.fields_get.return_value = {}
        conn.search_read.return_value = []
        return OdooToolHandler(app, conn, controller, config), conn

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fault", [XMLRPC_FAULT, JSON2_FAULT])
    async def test_recovers_message_id_from_chatter(self, handler, fault):
        h, conn = handler
        conn.search.side_effect = [[40], [43]]  # watermark, then the new message
        conn.call_method.side_effect = fault

        result = await h._handle_post_message_tool(
            model="helpdesk.ticket", record_id=7, body="<p>hi</p>"
        )

        assert result["success"] is True
        assert result["message_id"] == 43
        assert result["subtype"] == "Discussions"
        assert any("recovered from the chatter" in d for d in result["degraded_details"])
        # The lookup only accepts messages newer than the watermark, by this user
        domain = conn.search.call_args_list[1].args[1]
        assert ("id", ">", 40) in domain
        assert ("create_uid", "=", 5) in domain

    @pytest.mark.asyncio
    async def test_empty_chatter_watermark_is_zero(self, handler):
        h, conn = handler
        conn.search.side_effect = [[], [1]]
        conn.call_method.side_effect = self.XMLRPC_FAULT

        result = await h._handle_post_message_tool(model="res.partner", record_id=7, body="x")

        assert result["message_id"] == 1
        assert ("id", ">", 0) in conn.search.call_args_list[1].args[1]

    @pytest.mark.asyncio
    async def test_no_new_message_refuses_to_claim_success(self, handler):
        h, conn = handler
        conn.search.side_effect = [[40], []]
        conn.call_method.side_effect = self.XMLRPC_FAULT

        with pytest.raises(ValidationError, match="check helpdesk.ticket 7 before retrying"):
            await h._handle_post_message_tool(
                model="helpdesk.ticket", record_id=7, body="<p>hi</p>"
            )

    @pytest.mark.asyncio
    async def test_unknown_watermark_refuses_to_claim_success(self, handler):
        """If the chatter could not be read before posting, never guess."""
        h, conn = handler
        conn.search.side_effect = Exception("no access to mail.message")
        conn.call_method.side_effect = self.XMLRPC_FAULT

        with pytest.raises(ValidationError, match="retry would post it twice"):
            await h._handle_post_message_tool(
                model="helpdesk.ticket", record_id=7, body="<p>hi</p>"
            )
        assert conn.search.call_count == 1

    @pytest.mark.asyncio
    async def test_other_errors_are_not_recovered(self, handler):
        h, conn = handler
        conn.search.return_value = [40]
        conn.call_method.side_effect = Exception("Access denied: no write on helpdesk.ticket")

        with pytest.raises(ValidationError, match="Failed to post message"):
            await h._handle_post_message_tool(
                model="helpdesk.ticket", record_id=7, body="<p>hi</p>"
            )
        assert conn.search.call_count == 1
