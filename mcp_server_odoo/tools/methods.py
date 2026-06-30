"""Generic ORM method tool: execute_method.

Calls a public Odoo method on a model or recordset, the same way Odoo's own
external API (`execute_kw`) does. Nothing is reimplemented; we pass the call
straight through to the live Odoo via the transport-agnostic `call_method`.

Odoo's own access rights are the only gate: the connected user must be allowed
to run the method, and Odoo's RPC layer already refuses private (leading
underscore) methods. We mirror that one rule with a clear error and otherwise
stay out of the way.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from mcp.types import ToolAnnotations

from ..access_control import AccessControlError
from ..error_handling import ValidationError
from ..error_sanitizer import ErrorSanitizer
from ..logging_config import perf_logger
from ..odoo_connection import OdooConnectionError
from ..schemas import ExecuteMethodResult
from ._common import _current_sub, logger, run_blocking
from .wizards import WizardHandler, followup_descriptor, get_handler

_ACTION_SHAPE_KEYS = ("view_mode", "views", "target", "res_id", "domain")


def _unsupported_cta() -> str:
    """How to tell the caller to get an unsupported wizard supported.

    Kept configurable so the open-source server stays vendor-neutral: the
    SaaS/admin layer sets MCP_UNSUPPORTED_WIZARD_CTA to its own support route
    (e.g. 'Contact Pantalytics support to request this action.'). Default is a
    neutral hint for self-hosters.
    """
    return os.getenv(
        "MCP_UNSUPPORTED_WIZARD_CTA",
        "This server does not handle this wizard; you can complete the action "
        "directly in Odoo, or ask whoever operates this MCP server to add support.",
    ).strip()


def _classify_result(value: Any) -> str:
    """Bucket a method return into value / records / action.

    - an Odoo action dict -> 'action'. The reliable signal is a string `type`
      starting with `ir.actions.`; we also accept a `res_model` paired with an
      action-shaped key (view_mode/views/target/...) for the rare typeless
      action. A plain result dict that merely happens to carry a `res_model`
      key is NOT treated as an action.
    - list of ints (record ids) -> 'records'
    - anything else (bool, number, string, None, plain dict) -> 'value'
    """
    if isinstance(value, dict):
        type_val = value.get("type")
        type_is_action = isinstance(type_val, str) and type_val.startswith("ir.actions.")
        res_model_action = "res_model" in value and any(k in value for k in _ACTION_SHAPE_KEYS)
        if type_is_action or res_model_action:
            return "action"
        return "value"
    if isinstance(value, list) and value and all(isinstance(v, int) for v in value):
        return "records"
    return "value"


class MethodsToolsMixin:
    """execute_method tool."""

    def _register_methods_tools(self):
        """Register the execute_method tool with FastMCP."""

        @self.app.tool(
            title="Execute Odoo Method (call a standard Odoo action)",
            annotations=ToolAnnotations(
                # A method can do anything from read-only to destructive; we cannot
                # know in advance, so we flag the cautious defaults.
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def execute_method(
            model: str,
            method: str,
            ids: Optional[List[int]] = None,
            kwargs: Optional[Dict[str, Any]] = None,
            decision: Optional[Dict[str, Any]] = None,
            connection: Optional[str] = None,
        ) -> ExecuteMethodResult:
            """Call a public method on an Odoo model or recordset.

            This is the standard Odoo way to trigger an action that is not plain
            create/read/update/delete: confirming a sales order, posting an
            invoice, marking a CRM lead lost, validating a delivery, and so on.
            It runs Odoo's own method unchanged.

            Some methods return a wizard instead of completing (e.g. validating a
            delivery that is short on stock asks whether to create a backorder).
            For known wizards this tool finishes them with Odoo's own wizard when
            you pass the answer in `decision`. If you call without a decision, the
            wizard's fields are returned (`followup`) so you can read them and
            re-call with `decision` filled in. This two-step flow is stateless: it
            needs no live back-and-forth with your client.

            Args:
                model: Odoo model name, e.g. 'sale.order', 'account.move'.
                method: Public method to call, e.g. 'action_confirm',
                    'action_post', 'action_set_lost'. Private methods (names
                    starting with '_') are rejected, exactly as Odoo's API does.
                ids: Record ids to call the method on (the recordset). Omit for
                    a model-level (`@api.model`) method.
                kwargs: Keyword arguments for the method, if it takes any.
                decision: Answer for a follow-up wizard, e.g.
                    {"create_backorder": true} or {"journal_id": 7}. OMIT it on
                    the first call to discover the fields via `followup`; pass it
                    (re-calling the same method) to complete. Pass decision={} to
                    accept all of Odoo's defaults for an all-optional wizard
                    (e.g. register-payment: full residual, today, default
                    journal). An omitted decision discovers; any provided
                    decision -- even {} -- completes.
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                The raw return value, classified as a plain value, record ids, an
                Odoo action (wizard still needing a decision), or 'completed' when
                a known wizard was driven to the end for you.
            """
            result = await self._handle_execute_method_tool(
                model, method, ids, kwargs, decision=decision, connection_selector=connection
            )
            self._track_usage(_current_sub.get(), "execute_method")
            return ExecuteMethodResult(**result)

    async def _handle_execute_method_tool(
        self,
        model: str,
        method: str,
        ids: Optional[List[int]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        decision: Optional[Dict[str, Any]] = None,
        connection_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle execute_method tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context(
                connection_selector, writes=True
            )
            with perf_logger.track_operation("tool_execute_method", model=model):
                if not method or not method.strip():
                    raise ValidationError("method is required")
                # Mirror Odoo's own public/private boundary: the RPC layer refuses
                # underscore methods, so reject them up front with a clear message.
                if method.startswith("_"):
                    raise ValidationError(
                        f"Cannot call private method '{method}'. Only public Odoo "
                        "methods are callable, the same as Odoo's external API."
                    )

                # Touching a model at all needs read access; everything beyond that
                # is enforced by Odoo when the method runs.
                access_controller.validate_model_access(model, "read")

                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                value = await run_blocking(
                    connection, connection.call_method, model, method, ids=ids, **(kwargs or {})
                )

                kind = _classify_result(value)

                if kind == "action":
                    handler = get_handler(value)
                    if handler is not None:
                        # _drive_wizard issues further blocking RPC (handler.apply
                        # -> connection.*); run it off-loop under the same
                        # connection lock so the wizard completion can't block
                        # other tenants and can't race the transport.
                        return await run_blocking(
                            connection,
                            self._drive_wizard,
                            connection,
                            handler,
                            value,
                            decision,
                            model,
                            method,
                            ids or [],
                        )
                    # Known method, but it needs a follow-up wizard we have NOT
                    # validated. We refuse rather than guess: an un-vetted
                    # wizard completion against financial data is exactly where a
                    # silent wrong result hides. Flag it clearly as unsupported
                    # so the calling agent stops and surfaces the support CTA
                    # instead of treating this as done.
                    res_model = value.get("res_model") or value.get("type")
                    return {
                        "success": False,
                        "model": model,
                        "method": method,
                        "result_kind": "unsupported",
                        "result": None,
                        "action": value,
                        "followup": None,
                        "message": (
                            f"Not supported yet: {model}.{method} needs a follow-up "
                            f"step (the Odoo '{res_model}' wizard) that this server "
                            f"does not support, so nothing was changed. "
                            f"{_unsupported_cta()}"
                        ),
                    }

                if kind == "records":
                    summary = f"{model}.{method} returned {len(value)} record id(s)."
                else:
                    summary = f"{model}.{method} completed."

                return {
                    "success": True,
                    "model": model,
                    "method": method,
                    "result_kind": kind,
                    "result": value,
                    "action": None,
                    "followup": None,
                    "message": summary,
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in execute_method tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to execute {model}.{method}: {sanitized_msg}") from e

    def _drive_wizard(
        self,
        connection,
        handler: WizardHandler,
        action: Dict[str, Any],
        decision: Optional[Dict[str, Any]],
        model: str,
        method: str,
        ids: List[int],
    ) -> Dict[str, Any]:
        """Two-step follow-up for a known wizard, stateless by design.

        Mirrors the MCP 2026-07-28 stateless-elicitation shape (SEP-2322): a first
        call with no answer returns what to fill in (our `followup`, the analogue
        of `InputRequiredResult.inputRequests`); the caller re-issues the SAME
        call with the answer (our `decision`, the analogue of `inputResponses`).
        Any replica can serve either call, so this works on stateless HTTP and
        survives blue-green deploys. When the SDK ships InputRequiredResult we
        rename `followup`->inputRequests and `decision`->inputResponses; the flow
        is already this.

        The discover-vs-complete signal is *presence*, not truthiness:
        - `decision is None`  -> discover (return the fields to fill).
        - `decision is not None` (including `{}`) -> complete. `{}` means
          "accept all defaults", which is the only way to finish an all-optional
          wizard like register-payment. This matches the spec: re-issuing with
          inputResponses (even empty) completes the call.
        """
        data: Optional[Dict[str, Any]] = None

        if decision is not None:
            try:
                data = handler.schema(**decision).model_dump()
            except Exception as e:
                raise ValidationError(f"Invalid decision for {handler.res_model}: {e}") from e

        if data is not None:
            completion = handler.apply(connection, action, data, model, ids)
            comp_result = completion.get("result")
            via = f"(via {handler.res_model}.{completion.get('completion_method')})"
            # The completion can return ANOTHER action. Two cases:
            #  - a further KNOWN wizard -> genuinely needs another decision.
            #  - a plain window/navigation action (e.g. open the created
            #    account.payment) -> the wizard DID complete; that action is just
            #    Odoo navigating to the result. Report 'completed', not "review
            #    in Odoo" (which wrongly read as not-done for a paid invoice).
            if _classify_result(comp_result) == "action":
                next_handler = get_handler(comp_result)
                if next_handler is not None:
                    res_model = comp_result.get("res_model")
                    return {
                        "success": True,
                        "model": model,
                        "method": method,
                        "result_kind": "action",
                        "result": None,
                        "action": comp_result,
                        "followup": followup_descriptor(next_handler),
                        "message": (
                            f"{model}.{method} -> {completion.get('message')} but Odoo "
                            f"returned a further wizard ({res_model}); another decision "
                            f"is needed."
                        ),
                    }
                return {
                    "success": True,
                    "model": model,
                    "method": method,
                    "result_kind": "completed",
                    "result": comp_result,
                    "action": action,
                    "followup": None,
                    "message": (
                        f"{model}.{method} -> {completion.get('message')} {via} "
                        f"Odoo returned a navigation action to the resulting record."
                    ),
                }
            return {
                "success": True,
                "model": model,
                "method": method,
                "result_kind": "completed",
                "result": comp_result,
                "action": action,
                "followup": None,
                "message": f"{model}.{method} -> {completion.get('message')} {via}",
            }

        # Discover: return the fields to fill (re-call with `decision` to complete).
        return {
            "success": True,
            "model": model,
            "method": method,
            "result_kind": "action",
            "result": None,
            "action": action,
            "followup": followup_descriptor(handler),
            "message": (
                f"{model}.{method} needs input: {handler.prompt} "
                "Re-call with decision={...} (use decision={} to accept all defaults)."
            ),
        }
