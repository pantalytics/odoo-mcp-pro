"""Chatter messaging MCP tool: post_message."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.types import ToolAnnotations

from ..access_control import AccessControlError
from ..connection_protocol import OdooConnectionProtocol
from ..error_handling import NotFoundError, ValidationError
from ..error_sanitizer import ErrorSanitizer
from ..logging_config import perf_logger
from ..odoo_connection import OdooConnectionError
from ..schemas import PostMessageResult
from ._common import _current_sub, logger, run_blocking


class MessagingToolsMixin:
    """post_message tool and recordset-method helper."""

    def _register_messaging_tools(self):
        """Register messaging tool handlers with FastMCP."""

        # --- Chatter: post_message ---

        @self.app.tool(
            title="Post Chatter Message (Send Message / Log Note)",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,  # mt_comment sends real email; mt_note may also if partner_ids set
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def post_message(
            model: str,
            record_id: int,
            body: str,
            subject: Optional[str] = None,
            partner_ids: Optional[List[int]] = None,
            attachment_ids: Optional[List[int]] = None,
            subtype_xmlid: str = "mail.mt_comment",
            cc: Optional[str] = None,
            connection: Optional[str] = None,
        ) -> PostMessageResult:
            """Post a message in the chatter of any thread-enabled Odoo record.

            Equivalent to clicking 'Send Message' (subtype=mt_comment, default)
            or 'Log Note' (subtype=mt_note) in the Odoo UI. Sends synchronously
            within the same request — no waiting on the email queue cron.

            Args:
                model: Odoo model with chatter enabled — 'res.partner', 'crm.lead',
                    'sale.order', 'account.move', 'helpdesk.ticket', etc.
                record_id: ID of the record to post on.
                body: HTML body of the message. Plain strings are HTML-escaped by Odoo.
                subject: Optional subject line. Defaults to the record's display_name
                    when omitted on a non-note message.
                partner_ids: Explicit recipients (res.partner ids). Notifies them on
                    top of subscribed followers. NB: setting this on a note (mt_note)
                    still creates mail.notification + mail.mail for these partners.
                attachment_ids: ir.attachment ids to link to the message. Pre-create
                    via create_record on ir.attachment with {name, datas, res_model,
                    res_id} — this is required because inline byte transport over
                    XML-RPC fails.
                subtype_xmlid: 'mail.mt_comment' (default — sends email to followers)
                    or 'mail.mt_note' (silent internal note, hidden from portal users).
                cc: Comma-separated extra emails to notify (Odoo v19+ only).
                    On older Odoos this raises a clear error.
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                Posted mail.message details including per-recipient delivery state
                (mail.notification rows) and, if pan_outlook_pro is installed and the
                send went via Microsoft Graph, the Outlook message-id.
            """
            result = await self._handle_post_message_tool(
                model=model,
                record_id=record_id,
                body=body,
                subject=subject,
                partner_ids=partner_ids,
                attachment_ids=attachment_ids,
                subtype_xmlid=subtype_xmlid,
                cc=cc,
                connection_selector=connection,
            )
            self._track_usage(_current_sub.get(), "post_message")
            return PostMessageResult(**result)

    async def _call_record_method(
        self,
        connection: OdooConnectionProtocol,
        model: str,
        record_ids: List[int],
        method: str,
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Invoke a method on a recordset via the transport-agnostic call_method.

        Generic helper for tools that wrap an Odoo recordset method
        (`record.foo(...)` rather than CRUD). Works on both XML-RPC
        and JSON/2 transports.

        Future tools (post_invoice, confirm_sale_order, etc.) reuse this.
        """
        return await run_blocking(
            connection,
            connection.call_method,
            model,
            method,
            ids=list(record_ids),
            **(kwargs or {}),
        )

    async def _handle_post_message_tool(
        self,
        model: str,
        record_id: int,
        body: str,
        subject: Optional[str] = None,
        partner_ids: Optional[List[int]] = None,
        attachment_ids: Optional[List[int]] = None,
        subtype_xmlid: str = "mail.mt_comment",
        cc: Optional[str] = None,
        connection_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle post_message tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context(
                connection_selector, writes=True
            )
            with perf_logger.track_operation("tool_post_message", model=model):
                # Posting to chatter requires write access on the model
                access_controller.validate_model_access(model, "write")

                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                if not body or not body.strip():
                    raise ValidationError("body is required and cannot be empty")

                # Verify record exists
                existing = await run_blocking(
                    connection, connection.read, model, [record_id], ["id"]
                )
                if not existing:
                    raise NotFoundError(f"Record not found: {model} with ID {record_id}")

                # Build kwargs for message_post — only include fields the user set,
                # so we don't override Odoo's own defaults (e.g. subject from display_name).
                # body_is_html=True is essential over RPC: Odoo's message_post escapes
                # plain str bodies (it expects markupsafe.Markup for HTML), but Markup
                # objects can't traverse XML-RPC / JSON-RPC. Without this flag, "<p>x</p>"
                # arrives in the chatter as literal "&lt;p&gt;x&lt;/p&gt;".
                kwargs: Dict[str, Any] = {
                    "body": body,
                    "body_is_html": True,
                    "message_type": "comment",
                    "subtype_xmlid": subtype_xmlid,
                }
                if subject is not None:
                    kwargs["subject"] = subject
                if partner_ids:
                    kwargs["partner_ids"] = list(partner_ids)
                if attachment_ids:
                    kwargs["attachment_ids"] = list(attachment_ids)
                if cc:
                    # Odoo v19+ only — older Odoos raise:
                    # ValueError: Those values are not supported when posting or notifying: outgoing_email_to
                    kwargs["outgoing_email_to"] = cc

                raw = await self._call_record_method(
                    connection, model, [record_id], "message_post", kwargs
                )
                # message_post returns the new mail.message id; some transports
                # wrap singletons in a list — normalize.
                if isinstance(raw, list):
                    if not raw:
                        raise ValidationError("message_post returned empty result")
                    message_id = raw[0]
                else:
                    message_id = raw
                if not isinstance(message_id, int):
                    raise ValidationError(f"Unexpected message_post return: {raw!r}")

                # From here on the message exists in Odoo. The reads below only
                # enrich the response; they must never turn a successful post into
                # a reported failure. Some Odoo builds cannot serialise
                # mail.message-related responses over RPC (e.g. server-side
                # "TypeError: cannot marshal <class 'File'> objects" from Odoo's
                # OdooMarshaller, seen on Odoo Online), so each follow-up read is
                # tolerated individually: log the underlying error loudly and
                # degrade the detail instead of raising.
                degraded: List[str] = []

                # Read message back for subtype/attachment summary
                subtype_name: Optional[str] = None
                attachments: List[Any] = []
                outlook_msg_id: Optional[Any] = None
                try:
                    msg_fields = ["subtype_id", "attachment_ids"]
                    # x_microsoft_message_id only exists when pan_outlook_pro is installed
                    outlook_field = "x_microsoft_message_id"
                    try:
                        available = await run_blocking(
                            connection,
                            connection.fields_get,
                            "mail.message",
                            [outlook_field],
                            allfields=False,
                        )
                    except TypeError:
                        available = await run_blocking(
                            connection, connection.fields_get, "mail.message", [outlook_field]
                        )
                    except Exception:
                        available = {}
                    if outlook_field in (available or {}):
                        msg_fields.append(outlook_field)

                    msg_rows = await run_blocking(
                        connection, connection.read, "mail.message", [message_id], msg_fields
                    )
                    msg = msg_rows[0] if msg_rows else {}
                    subtype_pair = msg.get("subtype_id")
                    subtype_name = (
                        subtype_pair[1]
                        if isinstance(subtype_pair, list) and len(subtype_pair) > 1
                        else None
                    )
                    attachments = msg.get("attachment_ids") or []
                    outlook_msg_id = msg.get(outlook_field) if outlook_field in msg_fields else None
                    if outlook_msg_id is False:
                        outlook_msg_id = None
                except Exception:
                    logger.error(
                        "post_message: mail.message %s was posted to %s:%s but reading "
                        "the message back failed; returning success with degraded detail",
                        message_id,
                        model,
                        record_id,
                        exc_info=True,
                    )
                    degraded.append("message details")

                # Read notifications fan-out
                notifications: List[Dict[str, Any]] = []
                try:
                    notif_rows = await run_blocking(
                        connection,
                        connection.search_read,
                        "mail.notification",
                        [("mail_message_id", "=", message_id)],
                        [
                            "res_partner_id",
                            "notification_type",
                            "notification_status",
                            "failure_reason",
                        ],
                    )
                    for n in notif_rows:
                        p = n.get("res_partner_id") or [None, ""]
                        notifications.append(
                            {
                                "partner_id": p[0] if isinstance(p, list) else None,
                                "partner_name": p[1] if isinstance(p, list) and len(p) > 1 else "",
                                "type": n.get("notification_type") or "",
                                "status": n.get("notification_status") or "",
                                "failure_reason": n.get("failure_reason") or None,
                            }
                        )
                except Exception:
                    logger.error(
                        "post_message: mail.message %s was posted to %s:%s but reading "
                        "the notification fan-out failed; returning success with "
                        "degraded detail",
                        message_id,
                        model,
                        record_id,
                        exc_info=True,
                    )
                    degraded.append("notification status")

                base_url = (
                    getattr(connection, "_base_url", None)
                    or (self.config.url if self.config else "")
                ).rstrip("/")
                record_url = f"{base_url}/web#id={record_id}&model={model}&view_type=form"

                send_count = sum(1 for n in notifications if n["status"] == "sent")
                fail_count = sum(1 for n in notifications if n["status"] == "exception")
                summary_bits = [f"posted mail.message {message_id}"]
                if notifications:
                    summary_bits.append(
                        f"{len(notifications)} notification(s): {send_count} sent, {fail_count} failed"
                    )
                if outlook_msg_id:
                    summary_bits.append("sent via Microsoft Graph")
                if degraded:
                    summary_bits.append(
                        "the message was posted, but Odoo could not return "
                        f"{' and '.join(degraded)} (see server logs)"
                    )

                return {
                    "success": True,
                    "message_id": message_id,
                    "subtype": subtype_name,
                    "attachment_count": len(attachments),
                    "notifications": notifications,
                    "outlook_pro_message_id": outlook_msg_id,
                    "record_url": record_url,
                    "degraded_details": degraded,
                    "message": "; ".join(summary_bits),
                }

        except ValidationError:
            raise
        except NotFoundError as e:
            raise ValidationError(str(e)) from e
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in post_message tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to post message: {sanitized_msg}") from e
