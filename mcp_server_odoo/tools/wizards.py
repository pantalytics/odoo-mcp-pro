"""Drive Odoo wizards that business methods return, in three modes.

When a standard method (e.g. stock.picking.button_validate) returns an Odoo
wizard action instead of completing, we finish it with Odoo's OWN wizard:
create the transient model with the right context, then call its completion
method. None of Odoo's logic is reimplemented here.

Two-step and stateless: the caller either supplies the decision as a parameter
(we complete the wizard) or omits it (we return the available fields so the
caller re-calls with `decision`). This mirrors the MCP 2026-07-28 stateless
elicitation shape (InputRequiredResult / inputResponses); see
docs/adr/0002-stateful-sessions-and-elicitation.md. This module owns the
per-wizard schema and apply logic; the orchestration lives in methods.py.

Validated against live Odoo 17, 18 and 19 (the "top 5" business actions and
their Odoo-standard undo): sales confirm/cancel, invoice post/reset-or-reverse,
register/cancel payment, purchase confirm/cancel, delivery validate/return.
Cross-version notes captured in the apply functions (e.g. sale.order.cancel is
a wizard on 17/18 but a direct action on 19; account.move.reversal returns its
context as an unevaluated string).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..connection_protocol import OdooConnectionProtocol
from ..error_handling import ValidationError

# --- Per-wizard decision schemas (primitive fields only, per the MCP
#     elicitation spec: no nested objects or arrays of objects). ---


class BackorderDecision(BaseModel):
    """Follow-up for stock.backorder.confirmation."""

    create_backorder: bool = Field(
        description=(
            "Create a backorder for the unshipped quantity? Yes keeps the "
            "remaining items to deliver later; no cancels the backorder."
        ),
    )


class RegisterPaymentDecision(BaseModel):
    """Follow-up for account.payment.register. All optional: omit to take
    Odoo's computed defaults (full residual, today, default journal)."""

    journal_id: Optional[int] = Field(
        default=None, description="Payment journal id (e.g. Bank). Omit for Odoo's default."
    )
    amount: Optional[float] = Field(
        default=None, description="Amount to register. Omit to pay the full residual."
    )
    payment_date: Optional[str] = Field(
        default=None, description="Payment date as YYYY-MM-DD. Omit for today."
    )
    communication: Optional[str] = Field(
        default=None, description="Memo on the payment. Omit for Odoo's default."
    )


class CancelOrderDecision(BaseModel):
    """Follow-up for sale.order.cancel. No fields: providing a decision (even an
    empty {}) confirms the cancellation. On Odoo 17/18, cancelling a confirmed
    sales order opens this confirmation wizard; on 19 action_cancel cancels
    directly with no wizard, so this handler only ever runs on 17/18."""


class ReverseMovesDecision(BaseModel):
    """Follow-up for account.move.reversal (the credit-note / reversal of a
    posted move). All optional: omit to take Odoo's defaults."""

    reason: Optional[str] = Field(default=None, description="Reason shown on the credit note.")
    date: Optional[str] = Field(
        default=None, description="Reversal date as YYYY-MM-DD. Omit for today."
    )
    journal_id: Optional[int] = Field(
        default=None, description="Journal for the credit note. Omit for the move's own journal."
    )


class MergePartnersDecision(BaseModel):
    """Decision for the base.partner.merge.automatic.wizard (merge duplicate
    contacts). The contacts to merge are the recordset passed in `ids`; the one
    field here is which of them survives."""

    dst_partner_id: int = Field(
        description=(
            "Id of the contact to KEEP (the survivor). Must be one of the ids "
            "passed in `ids`. Every other contact in `ids` is merged into it "
            "(their messages, activities and references are moved over) and then "
            "removed. This CANNOT be undone."
        ),
    )


# --- Apply functions: create the wizard, call its completion method. ---


def _build_context(
    action: Dict[str, Any], origin_model: str, origin_ids: List[int]
) -> Dict[str, Any]:
    """Context for the wizard create.

    Starts from the action's OWN context and never overwrites it. Odoo sets the
    active_model / active_ids pairing itself: account.move.action_register_payment
    delegates to the move LINES, so its action carries
    active_model='account.move.line' with line ids -- overwriting active_ids with
    the move ids (our origin_ids) would make the wizard browse the wrong records.
    We only fill active_model / active_ids as a fallback for the rarer case where
    the action context omits them; then origin_model + origin_ids are consistent.
    """
    # Odoo sometimes returns a window action's `context` as an UNEVALUATED
    # string expression (e.g. account.move.action_reverse) rather than a dict.
    # We cannot evaluate that safely (it references the server-side env), so we
    # fall back to building the active_model/active_ids pairing from the origin
    # record, which is what the wizard's default_get needs anyway.
    raw = action.get("context")
    ctx = dict(raw) if isinstance(raw, dict) else {}
    ctx.setdefault("active_model", origin_model)
    ctx.setdefault("active_ids", list(origin_ids or []))
    if origin_ids:
        ctx.setdefault("active_id", origin_ids[0])
    return ctx


def _apply_backorder(
    connection: OdooConnectionProtocol,
    action: Dict[str, Any],
    data: Dict[str, Any],
    origin_model: str,
    origin_ids: List[int],
) -> Dict[str, Any]:
    ctx = _build_context(action, origin_model, origin_ids)
    # button_validate puts default_pick_ids in the action context; derive it from
    # the originating pickings if it did not survive the RPC round-trip.
    if "default_pick_ids" not in ctx and origin_ids:
        ctx["default_pick_ids"] = [(4, pid) for pid in origin_ids]
    # process()/process_cancel_backorder() act on context['button_validate_picking_ids'];
    # without it the wizard returns True and validates NOTHING. Odoo sets this key
    # when it opens the wizard; ensure it from the originating pickings as a guard
    # so we cannot silently no-op.
    if "button_validate_picking_ids" not in ctx and origin_ids:
        ctx["button_validate_picking_ids"] = list(origin_ids)
    wiz_id = connection.create("stock.backorder.confirmation", {}, context=ctx)
    method = "process" if data.get("create_backorder") else "process_cancel_backorder"
    # process()/process_cancel_backorder() read button_validate_picking_ids from
    # the CALL context (env.context), not from the wizard record, then re-run
    # button_validate(skip_backorder=True) on those pickings. Passing ctx only to
    # create() leaves the completion call's context empty, so process() hits its
    # `return True` no-op and validates nothing (a silent false success). The
    # context must travel with the completion call too. Verified against Odoo 19
    # (stock/wizard/stock_backorder_confirmation.py: process).
    result = connection.call_method(
        "stock.backorder.confirmation", method, ids=[wiz_id], context=ctx
    )
    verb = "with a backorder" if data.get("create_backorder") else "without a backorder"
    return {"completion_method": method, "result": result, "message": f"Validated {verb}."}


def _apply_register_payment(
    connection: OdooConnectionProtocol,
    action: Dict[str, Any],
    data: Dict[str, Any],
    origin_model: str,
    origin_ids: List[int],
) -> Dict[str, Any]:
    ctx = _build_context(action, origin_model, origin_ids)
    vals = {k: v for k, v in data.items() if v is not None}
    wiz_id = connection.create("account.payment.register", vals, context=ctx)
    # Pass ctx to the completion call too: action_create_payments reads the
    # wizard's own fields so it works without it, but a wizard completion can
    # read env.context (as stock's backorder process() does), so keep the
    # context consistent across create + complete.
    result = connection.call_method(
        "account.payment.register", "action_create_payments", ids=[wiz_id], context=ctx
    )
    return {
        "completion_method": "action_create_payments",
        "result": result,
        "message": "Payment registered.",
    }


def _apply_sale_cancel(
    connection: OdooConnectionProtocol,
    action: Dict[str, Any],
    data: Dict[str, Any],
    origin_model: str,
    origin_ids: List[int],
) -> Dict[str, Any]:
    # On Odoo 17/18 sale.order.action_cancel returns this confirmation wizard
    # (state stays 'sale' until it is completed). Its order_id comes from the
    # action context (default_order_id); create with that context, then confirm.
    ctx = _build_context(action, origin_model, origin_ids)
    wiz_id = connection.create("sale.order.cancel", {}, context=ctx)
    result = connection.call_method("sale.order.cancel", "action_cancel", ids=[wiz_id], context=ctx)
    return {"completion_method": "action_cancel", "result": result, "message": "Order cancelled."}


def _apply_reverse_moves(
    connection: OdooConnectionProtocol,
    action: Dict[str, Any],
    data: Dict[str, Any],
    origin_model: str,
    origin_ids: List[int],
) -> Dict[str, Any]:
    # account.move.action_reverse opens this wizard; reverse_moves() books the
    # credit note (the audit-safe undo of a posted/paid invoice, since Odoo
    # forbids deleting posted accounting entries).
    ctx = _build_context(action, origin_model, origin_ids)
    vals = {k: v for k, v in data.items() if v is not None}
    # action_reverse returns its context as an unevaluated string, so the
    # wizard's defaults (move_ids, the required journal_id) do not populate from
    # it over RPC. Make the wizard self-contained: target the origin move(s) and
    # default the credit-note journal to the invoice's own journal.
    vals.setdefault("move_ids", [(6, 0, list(origin_ids or []))])
    if "journal_id" not in vals and origin_ids:
        mv = connection.search_read("account.move", [["id", "=", origin_ids[0]]], ["journal_id"])
        if mv and mv[0].get("journal_id"):
            vals["journal_id"] = mv[0]["journal_id"][0]
    wiz_id = connection.create("account.move.reversal", vals, context=ctx)
    result = connection.call_method(
        "account.move.reversal", "reverse_moves", ids=[wiz_id], context=ctx
    )
    return {
        "completion_method": "reverse_moves",
        "result": result,
        "message": "Credit note created.",
    }


def _apply_merge_partners(
    connection: OdooConnectionProtocol,
    action: Dict[str, Any],
    data: Dict[str, Any],
    origin_model: str,
    origin_ids: List[int],
) -> Dict[str, Any]:
    """Merge duplicate res.partner records via Odoo's own merge wizard.

    Unlike the follow-up wizards above, this is an ENTRY wizard: no upstream
    method hands us an action, so `origin_ids` are the CONTACT ids to merge (the
    recordset the caller aimed the merge at), not wizard ids. We create the
    transient wizard with those partners and the chosen survivor, then call the
    wizard's own action_merge. action_merge performs the merge and returns an
    act_window that just reopens the (now finished) wizard -- the merge is done
    by the time it returns. Validated against live Odoo (see PR).
    """
    partner_ids = list(origin_ids or [])
    if len(partner_ids) < 2:
        raise ValidationError(
            "Merging contacts needs at least two contact ids in `ids` "
            "(the duplicates to merge together)."
        )
    dst = data.get("dst_partner_id")
    if dst not in partner_ids:
        raise ValidationError(
            f"dst_partner_id ({dst}) must be one of the contact ids in `ids` "
            f"{partner_ids} -- it is the contact to keep."
        )
    # default_get of the merge wizard reads active_ids (res.partner) to populate
    # partner_ids; we also pass partner_ids explicitly so it is self-contained.
    ctx = {
        "active_model": "res.partner",
        "active_ids": partner_ids,
        "active_id": partner_ids[0],
    }
    vals = {"partner_ids": [(6, 0, partner_ids)], "dst_partner_id": dst}
    wiz_id = connection.create("base.partner.merge.automatic.wizard", vals, context=ctx)
    result = connection.call_method(
        "base.partner.merge.automatic.wizard", "action_merge", ids=[wiz_id], context=ctx
    )
    merged = len(partner_ids) - 1
    return {
        "completion_method": "action_merge",
        "result": result,
        "message": f"Merged {merged} duplicate contact(s) into contact {dst}.",
    }


class WizardHandler:
    """Knows how to ask the follow-up and how to complete one wizard."""

    def __init__(
        self,
        res_model: str,
        schema: type[BaseModel],
        apply: Callable[..., Dict[str, Any]],
        prompt: str,
    ):
        self.res_model = res_model
        self.schema = schema
        self.apply = apply
        self.prompt = prompt


WIZARD_REGISTRY: Dict[str, WizardHandler] = {
    "stock.backorder.confirmation": WizardHandler(
        "stock.backorder.confirmation",
        BackorderDecision,
        _apply_backorder,
        "This delivery is not fully done. Create a backorder for the remaining quantity?",
    ),
    "account.payment.register": WizardHandler(
        "account.payment.register",
        RegisterPaymentDecision,
        _apply_register_payment,
        "Register a payment for this record? Set journal, amount and date, or accept the defaults.",
    ),
    "sale.order.cancel": WizardHandler(
        "sale.order.cancel",
        CancelOrderDecision,
        _apply_sale_cancel,
        "Cancel this sales order? Re-call with decision={} to confirm.",
    ),
    "account.move.reversal": WizardHandler(
        "account.move.reversal",
        ReverseMovesDecision,
        _apply_reverse_moves,
        "Create a credit note reversing this invoice? Set a reason/date or accept the defaults.",
    ),
}


def get_handler(action: Optional[Dict[str, Any]]) -> Optional[WizardHandler]:
    """Return the handler for a returned action dict, or None if unknown."""
    if not isinstance(action, dict):
        return None
    return WIZARD_REGISTRY.get(action.get("res_model"))


# --- Entry wizards: driven by a direct (model, method) call, not by an action
#     Odoo hands back. These do the work inside their own action_* method (Odoo's
#     merge wizard merges, then returns an act_window that just reopens itself),
#     so we intercept the call BEFORE hitting Odoo and route it through the same
#     discover/complete flow. Kept out of WIZARD_REGISTRY so the reopen action
#     they return is NOT mistaken for a further follow-up. ---

ENTRY_WIZARD_REGISTRY: Dict[Tuple[str, str], WizardHandler] = {
    ("base.partner.merge.automatic.wizard", "action_merge"): WizardHandler(
        "base.partner.merge.automatic.wizard",
        MergePartnersDecision,
        _apply_merge_partners,
        "Merge these contacts into one? Pass the contacts as `ids` and "
        "dst_partner_id (the contact to keep); the others are merged in and "
        "removed. This cannot be undone.",
    ),
}


def get_entry_handler(model: str, method: str) -> Optional[WizardHandler]:
    """Return the handler for a wizard invoked directly, or None if unknown."""
    return ENTRY_WIZARD_REGISTRY.get((model, method))


def followup_descriptor(handler: WizardHandler) -> Dict[str, Any]:
    """Describe a wizard's decision fields for the deferred (mode 3) response."""
    schema = handler.schema.model_json_schema()
    return {
        "wizard": handler.res_model,
        "decision_fields": schema.get("properties", {}),
        "hint": (
            "Re-call execute_method with decision={...} to complete this wizard, "
            "or use a client that supports MCP elicitation."
        ),
    }
