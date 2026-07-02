"""Tests for the wizard follow-up layer on execute_method.

Two-step, stateless: a decision either completes the wizard or, when omitted,
the wizard's fields are returned so the caller re-calls with `decision`. There
is no elicitation (see docs/adr/0001-stateless-no-elicitation.md).

Mock-based: they assert the create + completion call SEQUENCE matches the
Odoo 19 wizard API. They do NOT prove real Odoo behaviour (no live instance).
"""

from unittest.mock import Mock

import pytest

from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.tools import OdooToolHandler

BACKORDER_ACTION = {
    "type": "ir.actions.act_window",
    "res_model": "stock.backorder.confirmation",
    "context": {"default_pick_ids": [(4, 5)], "default_show_transfers": False},
}

PAYMENT_ACTION = {
    "type": "ir.actions.act_window",
    "res_model": "account.payment.register",
    "context": {},
}


class TestWizardFollowup:
    @pytest.fixture
    def mock_app(self):
        app = Mock()
        app.tool = Mock(side_effect=lambda **kwargs: lambda func: func)
        return app

    @pytest.fixture
    def mock_connection(self):
        conn = Mock()
        conn.is_authenticated = True
        conn._base_url = "http://localhost:8069"
        return conn

    @pytest.fixture
    def mock_access_controller(self):
        controller = Mock()
        controller.validate_model_access = Mock()
        return controller

    @pytest.fixture
    def handler(self, mock_app, mock_connection, mock_access_controller):
        config = Mock()
        config.url = "http://localhost:8069"
        return OdooToolHandler(mock_app, mock_connection, mock_access_controller, config)

    # --- Decision supplied up front -> complete the wizard (agent / n8n path) ---

    @pytest.mark.asyncio
    async def test_backorder_decision_yes_creates_backorder(self, handler, mock_connection):
        mock_connection.call_method.side_effect = [BACKORDER_ACTION, True]
        mock_connection.create.return_value = 99

        result = await handler._handle_execute_method_tool(
            "stock.picking", "button_validate", ids=[5], decision={"create_backorder": True}
        )

        assert result["result_kind"] == "completed"
        # Wizard created with the action context (carries default_pick_ids).
        model_arg, vals_arg = mock_connection.create.call_args.args
        assert model_arg == "stock.backorder.confirmation"
        assert vals_arg == {}
        assert "default_pick_ids" in mock_connection.create.call_args.kwargs["context"]
        # Completion method is process() for "yes".
        second = mock_connection.call_method.call_args_list[1]
        assert second.args[:2] == ("stock.backorder.confirmation", "process")
        assert second.kwargs["ids"] == [99]

    @pytest.mark.asyncio
    async def test_backorder_decision_no_cancels_backorder(self, handler, mock_connection):
        mock_connection.call_method.side_effect = [BACKORDER_ACTION, True]
        mock_connection.create.return_value = 99

        result = await handler._handle_execute_method_tool(
            "stock.picking", "button_validate", ids=[5], decision={"create_backorder": False}
        )

        assert result["result_kind"] == "completed"
        second = mock_connection.call_method.call_args_list[1]
        assert second.args[:2] == ("stock.backorder.confirmation", "process_cancel_backorder")

    @pytest.mark.asyncio
    async def test_register_payment_decision_passes_vals_and_context(
        self, handler, mock_connection
    ):
        mock_connection.call_method.side_effect = [PAYMENT_ACTION, {"payment": 1}]
        mock_connection.create.return_value = 77

        result = await handler._handle_execute_method_tool(
            "account.move",
            "action_register_payment",
            ids=[10],
            decision={"journal_id": 7, "amount": 100.0, "payment_date": "2026-06-18"},
        )

        assert result["result_kind"] == "completed"
        model_arg, vals_arg = mock_connection.create.call_args.args
        assert model_arg == "account.payment.register"
        # Only non-null fields are passed as vals.
        assert vals_arg == {"journal_id": 7, "amount": 100.0, "payment_date": "2026-06-18"}
        # active_model/active_ids pinned from the originating invoice.
        ctx = mock_connection.create.call_args.kwargs["context"]
        assert ctx["active_model"] == "account.move"
        assert ctx["active_ids"] == [10]
        second = mock_connection.call_method.call_args_list[1]
        assert second.args[:2] == ("account.payment.register", "action_create_payments")

    @pytest.mark.asyncio
    async def test_invalid_decision_raises(self, handler, mock_connection):
        mock_connection.call_method.side_effect = [BACKORDER_ACTION]

        with pytest.raises(ValidationError, match="Invalid decision"):
            await handler._handle_execute_method_tool(
                "stock.picking", "button_validate", ids=[5], decision={"wrong_field": 1}
            )

    # --- No decision -> defer with the choices ---

    @pytest.mark.asyncio
    async def test_no_decision_defers_with_fields(self, handler, mock_connection):
        mock_connection.call_method.side_effect = [BACKORDER_ACTION]

        result = await handler._handle_execute_method_tool(
            "stock.picking", "button_validate", ids=[5]
        )

        assert result["result_kind"] == "action"
        assert "create_backorder" in result["followup"]["decision_fields"]
        assert result["result"] is None  # action lives in `action`, not duplicated
        mock_connection.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_decision_completes_with_defaults(self, handler, mock_connection):
        """For an all-optional wizard, decision={} means 'accept all defaults'
        and COMPLETES -- the only way to finish register-payment on defaults.
        Mirrors SEP-2322: re-issuing with (even empty) inputResponses completes."""
        mock_connection.call_method.side_effect = [PAYMENT_ACTION, {"payment": 1}]
        mock_connection.create.return_value = 77

        result = await handler._handle_execute_method_tool(
            "account.move", "action_register_payment", ids=[10], decision={}
        )

        assert result["result_kind"] == "completed"
        mock_connection.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_decision_on_required_field_wizard_errors(self, handler, mock_connection):
        """decision={} on a wizard with a REQUIRED field (backorder needs the
        yes/no choice) is an invalid payload: clear error, nothing created."""
        mock_connection.call_method.side_effect = [BACKORDER_ACTION]

        with pytest.raises(ValidationError, match="Invalid decision"):
            await handler._handle_execute_method_tool(
                "stock.picking", "button_validate", ids=[5], decision={}
            )

        mock_connection.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_omitted_decision_defers(self, handler, mock_connection):
        """No decision at all (None) -> discover: return the fields, change nothing."""
        mock_connection.call_method.side_effect = [BACKORDER_ACTION]

        result = await handler._handle_execute_method_tool(
            "stock.picking", "button_validate", ids=[5]
        )

        assert result["result_kind"] == "action"
        assert result["followup"] is not None
        mock_connection.create.assert_not_called()

    # --- Register payment specifics ---

    @pytest.mark.asyncio
    async def test_register_payment_partial_decision_strips_none_vals(
        self, handler, mock_connection
    ):
        """A partial decision passes only the set fields; None fields are dropped."""
        mock_connection.call_method.side_effect = [PAYMENT_ACTION, {"payment": 1}]
        mock_connection.create.return_value = 77

        result = await handler._handle_execute_method_tool(
            "account.move", "action_register_payment", ids=[10], decision={"journal_id": 7}
        )

        assert result["result_kind"] == "completed"
        _, vals_arg = mock_connection.create.call_args.args
        assert vals_arg == {"journal_id": 7}

    @pytest.mark.asyncio
    async def test_register_payment_preserves_action_active_ids(self, handler, mock_connection):
        """Bug guard: do not clobber active_model/active_ids the action set."""
        action = {
            "type": "ir.actions.act_window",
            "res_model": "account.payment.register",
            "context": {"active_model": "account.move.line", "active_ids": [101, 102]},
        }
        mock_connection.call_method.side_effect = [action, {"payment": 1}]
        mock_connection.create.return_value = 77

        await handler._handle_execute_method_tool(
            "account.move", "action_register_payment", ids=[10], decision={"amount": 50.0}
        )

        ctx = mock_connection.create.call_args.kwargs["context"]
        # The line-ids context Odoo set must survive, NOT be overwritten by [10].
        assert ctx["active_model"] == "account.move.line"
        assert ctx["active_ids"] == [101, 102]

    @pytest.mark.asyncio
    async def test_decision_ignores_unknown_keys(self, handler, mock_connection):
        """Extra keys in decision are ignored (pydantic extra=ignore default)."""
        mock_connection.call_method.side_effect = [BACKORDER_ACTION, True]
        mock_connection.create.return_value = 99

        result = await handler._handle_execute_method_tool(
            "stock.picking",
            "button_validate",
            ids=[5],
            decision={"create_backorder": True, "bogus": 9},
        )

        assert result["result_kind"] == "completed"
        _, vals_arg = mock_connection.create.call_args.args
        assert "bogus" not in vals_arg

    @pytest.mark.asyncio
    async def test_followup_descriptor_payment_fields(self, handler, mock_connection):
        mock_connection.call_method.side_effect = [PAYMENT_ACTION]

        result = await handler._handle_execute_method_tool(
            "account.move", "action_register_payment", ids=[10]
        )

        fields = result["followup"]["decision_fields"]
        assert set(fields) == {"journal_id", "amount", "payment_date", "communication"}
        assert result["followup"]["wizard"] == "account.payment.register"

    @pytest.mark.asyncio
    async def test_completed_passes_completion_result_through(self, handler, mock_connection):
        """The completion call's return value is surfaced in `result`."""
        payment = {"some": "payment-summary"}  # not an action (no type/view keys)
        mock_connection.call_method.side_effect = [PAYMENT_ACTION, payment]
        mock_connection.create.return_value = 77

        result = await handler._handle_execute_method_tool(
            "account.move", "action_register_payment", ids=[10], decision={"amount": 5.0}
        )

        assert result["result_kind"] == "completed"
        assert result["result"] == payment

    @pytest.mark.asyncio
    async def test_chained_wizard_not_marked_completed(self, handler, mock_connection):
        """If the completion returns another action, do not claim 'completed'."""
        chained = {
            "type": "ir.actions.act_window",
            "res_model": "account.payment.register",
            "target": "new",
        }
        mock_connection.call_method.side_effect = [BACKORDER_ACTION, chained]
        mock_connection.create.return_value = 99

        result = await handler._handle_execute_method_tool(
            "stock.picking", "button_validate", ids=[5], decision={"create_backorder": True}
        )

        assert result["result_kind"] == "action"
        assert result["action"] == chained


SALE_CANCEL_ACTION = {
    "type": "ir.actions.act_window",
    "res_model": "sale.order.cancel",
    "context": {"default_order_id": 5},
}

# action_reverse returns its context as an UNEVALUATED STRING (real Odoo
# behaviour) -- exercises the _build_context string fallback.
REVERSAL_ACTION = {
    "type": "ir.actions.act_window",
    "res_model": "account.move.reversal",
    "context": "{'default_move_ids': [Command.set(active_ids)]}",
}


class TestUndoWizards:
    """The Odoo-standard 'undo' wizards: cancel a confirmed sales order
    (v17/18) and reverse a posted invoice with a credit note (all versions)."""

    @pytest.fixture
    def mock_app(self):
        app = Mock()
        app.tool = Mock(side_effect=lambda **kwargs: lambda func: func)
        return app

    @pytest.fixture
    def mock_connection(self):
        conn = Mock()
        conn.is_authenticated = True
        conn._base_url = "http://localhost:8069"
        return conn

    @pytest.fixture
    def handler(self, mock_app, mock_connection):
        ac = Mock()
        ac.validate_model_access = Mock()
        config = Mock()
        config.url = "http://localhost:8069"
        return OdooToolHandler(mock_app, mock_connection, ac, config)

    @pytest.mark.asyncio
    async def test_sale_cancel_wizard_completes(self, handler, mock_connection):
        """v17/18: action_cancel returns the sale.order.cancel wizard; a decision
        drives it to completion via the wizard's action_cancel."""
        mock_connection.call_method.side_effect = [SALE_CANCEL_ACTION, True]
        mock_connection.create.return_value = 50

        result = await handler._handle_execute_method_tool(
            "sale.order", "action_cancel", ids=[5], decision={}
        )

        assert result["result_kind"] == "completed"
        assert mock_connection.create.call_args.args[0] == "sale.order.cancel"
        # second call_method is the wizard completion
        assert mock_connection.call_method.call_args.args[:2] == (
            "sale.order.cancel",
            "action_cancel",
        )

    @pytest.mark.asyncio
    async def test_sale_cancel_deferred_lists_no_required_fields(self, handler, mock_connection):
        """Omitting the decision returns the followup (confirm with decision={})."""
        mock_connection.call_method.side_effect = [SALE_CANCEL_ACTION]
        result = await handler._handle_execute_method_tool("sale.order", "action_cancel", ids=[5])
        assert result["result_kind"] == "action"
        assert result["followup"]["wizard"] == "sale.order.cancel"
        mock_connection.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_reverse_moves_builds_self_contained_credit_note(self, handler, mock_connection):
        """action_reverse's context is an unevaluated string, so the handler must
        target the origin move and default the journal itself."""
        mock_connection.call_method.side_effect = [REVERSAL_ACTION, {"refund": 1}]
        mock_connection.create.return_value = 60
        mock_connection.search_read.return_value = [{"journal_id": [7, "Customer Invoices"]}]

        result = await handler._handle_execute_method_tool(
            "account.move", "action_reverse", ids=[10], decision={"reason": "wrong amount"}
        )

        assert result["result_kind"] == "completed"
        model, vals = (
            mock_connection.create.call_args.args[0],
            mock_connection.create.call_args.args[1],
        )
        assert model == "account.move.reversal"
        assert vals["move_ids"] == [(6, 0, [10])]  # self-contained, not from the string ctx
        assert vals["journal_id"] == 7  # defaulted from the invoice
        assert vals["reason"] == "wrong amount"
        assert mock_connection.call_method.call_args.args[:2] == (
            "account.move.reversal",
            "reverse_moves",
        )


class TestMergeContactsEntryWizard:
    """Contact merge is an ENTRY wizard: the caller invokes the wizard's
    action_merge directly (no upstream Odoo action), `ids` are the contacts to
    merge, and the single decision is which one survives. The raw method is NEVER
    fired to 'discover' -- that would already perform the merge."""

    @pytest.fixture
    def mock_app(self):
        app = Mock()
        app.tool = Mock(side_effect=lambda **kwargs: lambda func: func)
        return app

    @pytest.fixture
    def mock_connection(self):
        conn = Mock()
        conn.is_authenticated = True
        conn._base_url = "http://localhost:8069"
        return conn

    @pytest.fixture
    def handler(self, mock_app, mock_connection):
        ac = Mock()
        ac.validate_model_access = Mock()
        config = Mock()
        config.url = "http://localhost:8069"
        return OdooToolHandler(mock_app, mock_connection, ac, config)

    @pytest.mark.asyncio
    async def test_no_decision_defers_without_touching_odoo(self, handler, mock_connection):
        """Discover must NOT call the wizard (no merge on discovery)."""
        result = await handler._handle_execute_method_tool(
            "base.partner.merge.automatic.wizard", "action_merge", ids=[35, 36]
        )

        assert result["result_kind"] == "action"
        assert result["followup"]["wizard"] == "base.partner.merge.automatic.wizard"
        assert "dst_partner_id" in result["followup"]["decision_fields"]
        mock_connection.call_method.assert_not_called()
        mock_connection.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_decision_merges_into_survivor(self, handler, mock_connection):
        """A decision creates the wizard with the contacts + survivor and calls
        action_merge once. action_merge returns a reopen action; the merge is
        already done, so we report 'completed', not 'unsupported'."""
        reopen = {
            "type": "ir.actions.act_window",
            "res_model": "base.partner.merge.automatic.wizard",
            "res_id": 1,
        }
        mock_connection.call_method.return_value = reopen
        mock_connection.create.return_value = 1

        result = await handler._handle_execute_method_tool(
            "base.partner.merge.automatic.wizard",
            "action_merge",
            ids=[35, 36],
            decision={"dst_partner_id": 35},
        )

        assert result["result_kind"] == "completed"
        model_arg, vals_arg = mock_connection.create.call_args.args
        assert model_arg == "base.partner.merge.automatic.wizard"
        assert vals_arg == {"partner_ids": [(6, 0, [35, 36])], "dst_partner_id": 35}
        ctx = mock_connection.create.call_args.kwargs["context"]
        assert ctx["active_model"] == "res.partner"
        assert ctx["active_ids"] == [35, 36]
        # Exactly one wizard call: action_merge on the created wizard.
        assert mock_connection.call_method.call_count == 1
        assert mock_connection.call_method.call_args.args[:2] == (
            "base.partner.merge.automatic.wizard",
            "action_merge",
        )
        assert mock_connection.call_method.call_args.kwargs["ids"] == [1]

    @pytest.mark.asyncio
    async def test_survivor_must_be_in_ids(self, handler, mock_connection):
        """dst_partner_id outside `ids` is refused before any merge runs."""
        with pytest.raises(ValidationError, match="must be one of the contact ids"):
            await handler._handle_execute_method_tool(
                "base.partner.merge.automatic.wizard",
                "action_merge",
                ids=[35, 36],
                decision={"dst_partner_id": 99},
            )

        mock_connection.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_needs_at_least_two_contacts(self, handler, mock_connection):
        """A single id (e.g. someone passing a wizard id the old way) cannot
        silently merge; it errors before creating anything."""
        with pytest.raises(ValidationError, match="at least two contact ids"):
            await handler._handle_execute_method_tool(
                "base.partner.merge.automatic.wizard",
                "action_merge",
                ids=[1],
                decision={"dst_partner_id": 1},
            )

        mock_connection.create.assert_not_called()
