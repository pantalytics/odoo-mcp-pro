"""Binary field upload MCP tool."""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from mcp.types import ToolAnnotations

from ..access_control import AccessControlError
from ..error_handling import NotFoundError, ValidationError
from ..error_sanitizer import ErrorSanitizer
from ..logging_config import perf_logger
from ..odoo_connection import OdooConnectionError
from ..schemas import BinaryFieldResult
from ._common import (
    _AVATAR_FIELD_RE,
    _BINARY_UPLOAD_SEMAPHORE,
    MAX_BINARY_SIZE_BYTES,
    _current_sub,
    logger,
    run_blocking,
)


class BinaryToolsMixin:
    """set_binary_field tool."""

    def _register_binary_tools(self):
        """Register binary field tool handlers with FastMCP."""

        # --- Binary Field Upload ---

        @self.app.tool(
            title="Set Binary Field (Upload Image/File to a Record Field)",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def set_binary_field(
            model: str,
            record_id: int,
            field_name: str,
            source: str,
            connection: Optional[str] = None,
        ) -> BinaryFieldResult:
            """Upload bytes into a Binary or Image field on an existing record.

            Use this for: avatar/logo on res.partner, product images on product.template,
            company logo, attachment bytes on custom Binary fields.

            IMPORTANT — bytes must NOT pass through the model. `source` must be an
            http(s) URL; the server fetches the bytes from that URL directly and
            streams them to Odoo. Do NOT base64-encode a local file and paste it
            into this call — LLMs are for reasoning, not binary transport.

            If the user has a local file without a URL, direct them to upload it
            somewhere reachable first: a Google Drive share link (direct download),
            Dropbox direct link, S3 pre-signed URL, Imgur, etc. They then give you
            the URL.

            For BULK import of files/documents into Odoo, use the Documents app
            (`documents.document`) — users can drop files into a Documents folder
            directly through the Odoo UI or a share-link, and you can then query
            the resulting records via search_records.

            For res.partner avatars: pass field_name='image_1920'. The avatar_*
            fields are computed from image_1920 and auto-resize. If you pass
            'avatar_1920' this tool auto-redirects to 'image_1920' and warns.

            For attaching PDFs/documents to a specific record (e.g. a bonnetje
            on a sale.order), use create_record on ir.attachment with
            {name, datas, res_model, res_id} instead.

            Args:
                model: Odoo model name (e.g. 'res.partner', 'product.template')
                record_id: ID of the record to update
                field_name: Binary or Image field name on that model
                source: http(s) URL the server will fetch. Max 25 MB.
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                Written field name, size in bytes, and record URL.
            """
            result = await self._handle_set_binary_field_tool(
                model, record_id, field_name, source, connection
            )
            self._track_usage(_current_sub.get(), "set_binary_field")
            return BinaryFieldResult(**result)

    async def _handle_set_binary_field_tool(
        self,
        model: str,
        record_id: int,
        field_name: str,
        source: str,
        connection_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle set_binary_field tool request.

        Fetches bytes from `source` (http(s) URL only — data: URIs are rejected
        so bytes never pass through the LLM), validates the target field is
        Binary/Image, auto-redirects avatar_* writes to image_1920, and writes
        the base64 string via connection.write.
        """
        try:
            async with _BINARY_UPLOAD_SEMAPHORE:
                connection, access_controller, sub = await self._get_user_context(
                    connection_selector, writes=True
                )
                with perf_logger.track_operation("tool_set_binary_field", model=model):
                    access_controller.validate_model_access(model, "write")
                    if not connection.is_authenticated:
                        raise ValidationError("Not authenticated with Odoo")
                    if not field_name:
                        raise ValidationError("field_name is required")
                    if not source:
                        raise ValidationError("source is required (http(s) URL)")

                    # --- Fetch bytes from URL ---
                    # Reject data: URIs: they would force the LLM to carry the full
                    # base64 payload in the tool call, which defeats the purpose of
                    # this tool. The user should upload the file somewhere reachable
                    # (Drive/Dropbox/S3/etc.) and pass the URL.
                    if source.startswith("data:"):
                        raise ValidationError(
                            "data: URIs are not accepted — bytes must not pass through the "
                            "LLM. Upload the file to a reachable URL (Google Drive share, "
                            "Dropbox direct link, S3 pre-signed URL, etc.) and pass the URL."
                        )
                    parsed = urlparse(source)
                    if parsed.scheme not in ("http", "https"):
                        raise ValidationError(
                            f"source must be an http(s) URL, got scheme '{parsed.scheme}'"
                        )
                    if not parsed.netloc:
                        raise ValidationError("source URL is missing a host")
                    try:
                        async with httpx.AsyncClient(
                            timeout=30.0,
                            follow_redirects=True,
                            max_redirects=5,
                        ) as client:
                            chunks: List[bytes] = []
                            total = 0
                            async with client.stream("GET", source) as resp:
                                resp.raise_for_status()
                                async for chunk in resp.aiter_bytes(chunk_size=65536):
                                    total += len(chunk)
                                    if total > MAX_BINARY_SIZE_BYTES:
                                        raise ValidationError(
                                            f"Source exceeds max size of "
                                            f"{MAX_BINARY_SIZE_BYTES // (1024 * 1024)} MB"
                                        )
                                    chunks.append(chunk)
                            raw_bytes = b"".join(chunks)
                    except ValidationError:
                        raise
                    except httpx.HTTPError as e:
                        raise ValidationError(f"Failed to fetch source URL: {e}") from e

                    if not raw_bytes:
                        raise ValidationError("Source produced zero bytes")

                    # --- Validate record exists ---
                    existing = await run_blocking(
                        connection, connection.read, model, [record_id], ["id"]
                    )
                    if not existing:
                        raise NotFoundError(f"Record not found: {model} with ID {record_id}")

                    # --- Validate field type + auto-redirect avatar_* ---
                    fields_info = await run_blocking(connection, connection.fields_get, model)
                    if not isinstance(fields_info, dict):
                        raise ValidationError(f"Could not introspect fields of {model}")

                    target_field = field_name
                    warning: Optional[str] = None

                    # Avatar fields on avatar.mixin are compute-only without inverse;
                    # writing to them is a silent no-op. Redirect to image_1920 if present.
                    if _AVATAR_FIELD_RE.match(field_name) and "image_1920" in fields_info:
                        target_field = "image_1920"
                        warning = (
                            f"'{field_name}' is a computed field without an inverse; "
                            f"wrote to 'image_1920' instead (avatar/image variants recompute automatically)"
                        )

                    # product.product.image_1920 has a fall-through inverse: if the
                    # template image is empty OR the template has only one active
                    # variant, the write lands on product.template instead of the
                    # variant. Use 'image_variant_1920' to force variant-specific
                    # storage. Don't auto-redirect (user may legitimately want the
                    # template-wide write); just warn.
                    if (
                        model == "product.product"
                        and field_name == "image_1920"
                        and "image_variant_1920" in fields_info
                    ):
                        warning = (
                            "writes to product.product.image_1920 may fall through to "
                            "product.template (if template image is empty or only one "
                            "active variant exists). Use field_name='image_variant_1920' "
                            "for guaranteed variant-specific storage."
                        )

                    if target_field not in fields_info:
                        raise ValidationError(
                            f"Field '{target_field}' does not exist on model '{model}'"
                        )

                    ftype = fields_info[target_field].get("type")
                    if ftype not in ("binary", "image"):
                        raise ValidationError(
                            f"Field '{target_field}' is type '{ftype}', not binary/image"
                        )
                    if fields_info[target_field].get("readonly"):
                        raise ValidationError(f"Field '{target_field}' on '{model}' is readonly")

                    # --- Write (Odoo ORM creates/updates backing ir.attachment) ---
                    b64 = base64.b64encode(raw_bytes).decode("ascii")
                    success = await run_blocking(
                        connection, connection.write, model, [record_id], {target_field: b64}
                    )

                    base_url = (
                        getattr(connection, "_base_url", None)
                        or (self.config.url if self.config else "")
                    ).rstrip("/")
                    record_url = f"{base_url}/web#id={record_id}&model={model}&view_type=form"

                    message = f"Wrote {len(raw_bytes)} bytes to {model}({record_id}).{target_field}"
                    if warning:
                        message = f"{message}. Note: {warning}"

                    return {
                        "success": bool(success),
                        "model": model,
                        "record_id": record_id,
                        "field": target_field,
                        "size_bytes": len(raw_bytes),
                        "url": record_url,
                        "message": message,
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
            logger.error(f"Error in set_binary_field tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to set binary field: {sanitized_msg}") from e
