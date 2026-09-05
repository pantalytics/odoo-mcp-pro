# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
"""The one MCP App: register a payment through a form in the chat.

MCP Apps (extension ``io.modelcontextprotocol/ui``) lets a tool carry a
``ui://`` resource -- an HTML document the host renders in a sandboxed iframe
next to the tool result. This module ships exactly one, on purpose (ADR 0004):
the register-payment wizard as a real form with journal, amount, date and
memo, instead of a JSON description the model reads and re-calls with.

The tool is ``register_payment``: ``execute_method`` with the method fixed to
``action_register_payment`` and the form bound to it. It degrades the way the
extension spec requires -- a client that did not negotiate Apps gets the same
``followup`` dict (or, on 2026-07-28 with form elicitation, the
``input_required`` form of ``input_required.py``), and an explicit
``decision`` completes it like any other wizard. The HTML lives next to this
file, bundled in one document with no external origins, so the host's
default CSP (``connect-src 'none'``) is enough.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mcp.server.apps import Apps, client_supports_apps
from mcp.server.mcpserver import Context
from mcp.types import InputRequiredResult, ToolAnnotations

from ..schemas import ExecuteMethodResult
from ._common import _current_sub

REGISTER_PAYMENT_UI = "ui://odoo/register-payment.html"
"""The form's resource URI; the tool's ``_meta.ui.resourceUri`` points here."""

_HTML = Path(__file__).with_name("register_payment.html")


def register_payment_html() -> str:
    """The bundled form, read from disk each time so a packaged wheel serves it too."""
    return _HTML.read_text(encoding="utf-8")


def odoo_apps() -> Apps:
    """The Apps extension carrying our ``ui://`` resources.

    Passed to ``MCPServer(extensions=[...])`` by ``create_fastmcp_app``; that
    is what advertises the capability and serves the resource. The tool
    itself is bound by the handler (``RegisterPaymentToolsMixin``), which
    needs the Odoo connection the app does not have at construction time.
    """
    apps = Apps()
    apps.add_html_resource(
        REGISTER_PAYMENT_UI,
        register_payment_html(),
        title="Register payment",
        description="Form for Odoo's register-payment wizard (account.payment.register).",
    )
    return apps


class RegisterPaymentToolsMixin:
    """``register_payment``: the wizard behind ``account.move.action_register_payment``."""

    def _register_payment_tools(self):
        """Register the UI-bound register_payment tool."""

        @self.tool(  # type: ignore[attr-defined]
            title="Register Payment (form)",
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=False,
                open_world_hint=True,
            ),
            meta={"ui": {"resourceUri": REGISTER_PAYMENT_UI}},
        )
        async def register_payment(
            model: str,
            ids: List[int],
            decision: Optional[Dict[str, Any]] = None,
            connection: Optional[str] = None,
            ctx: Optional[Context] = None,  # injected by the SDK, never by the model
        ) -> Union[ExecuteMethodResult, InputRequiredResult]:
            """Register a payment on posted invoices or bills, through a form.

            Runs Odoo's own register-payment wizard (`account.move.
            action_register_payment`). Prefer this over `execute_method` for
            payments: in a client that supports MCP Apps the user gets a form
            with journal, amount, date and memo right in the chat and submits
            it there; the result comes back to you as usual. Elsewhere it
            behaves exactly like `execute_method`: call without `decision` to
            see the wizard's fields (`followup`), or pass `decision={}` to
            accept Odoo's defaults (full residual, today, default journal).

            Args:
                model: 'account.move' for invoices and bills.
                ids: Ids of the posted invoices/bills to pay.
                decision: The wizard's answer: {"journal_id": 7, "amount": 100.0,
                    "payment_date": "2026-09-05", "communication": "..."}, any
                    subset, or {} for Odoo's defaults. Omit to ask.
                connection: Optional. Target a specific Odoo connection by the
                    id from server_info's `connections` list. Hosted
                    multi-tenant only.

            Returns:
                The same shape as `execute_method`: 'completed' once the payment
                is registered, 'action' with `followup` while the wizard still
                needs an answer, 'declined' when the user cancelled the form.
            """
            # When the host renders our form, the answer arrives through the
            # form's own tools/call, so the wizard must come back as the dict the
            # form reads -- not as an input_required round trip the form never
            # sees. Dropping ctx is what keeps input_required.py out of it.
            result = await self._handle_execute_method_tool(  # type: ignore[attr-defined]
                model,
                "action_register_payment",
                ids,
                None,
                decision=decision,
                connection_selector=connection,
                ctx=None if ctx is not None and client_supports_apps(ctx) else ctx,
            )
            self._track_usage(_current_sub.get(), "register_payment")  # type: ignore[attr-defined]
            if isinstance(result, InputRequiredResult):
                return result
            return ExecuteMethodResult(**result)
