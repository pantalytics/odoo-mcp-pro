# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Part of odoo-mcp-pro. This file stays under the Mozilla Public License 2.0;
# see LICENSE.MPL-2.0.
"""Image read MCP tool.

Returns Odoo image fields as real pictures (MCP ``ImageContent``) instead of
the base64 blob that every other read path deliberately filters out. See
docs/adr/0003-visuals-images-and-mcp-apps.md for the wider plan.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict, List, Optional

from mcp.types import ContentBlock, ImageContent, TextContent, ToolAnnotations

from ..access_control import AccessControlError
from ..error_handling import NotFoundError, ValidationError
from ..error_sanitizer import ErrorSanitizer
from ..logging_config import perf_logger
from ..odoo_connection import OdooConnectionError
from ._common import (
    MAX_IMAGE_BYTES,
    _current_sub,
    logger,
    run_blocking,
    validate_access,
)

# Odoo's own stored image variants, smallest to largest. Odoo computes these
# from image_1920, so asking for a small one is free: no server-side resizing,
# no imaging dependency.
IMAGE_SIZES = (128, 256, 512, 1024, 1920)
DEFAULT_IMAGE_SIZE = 512

# Preference order when the caller does not name a field: the requested size
# first, then the remaining variants smallest-first (cheap before expensive),
# then the bare `image` field some custom models use.
_FALLBACK_FIELDS = tuple(f"image_{size}" for size in IMAGE_SIZES) + ("image",)

# Mime types a host can actually render. Sniffed from the bytes, never guessed
# from the field name: Odoo stores whatever was uploaded.
_SUPPORTED_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
# Recognized but not renderable by MCP hosts. Named separately so the error can
# say what was found instead of "unknown format".
_UNSUPPORTED_SIGNATURES = (
    (b"BM", "BMP"),
    (b"II*\x00", "TIFF"),
    (b"MM\x00*", "TIFF"),
    (b"%PDF-", "PDF"),
    (b"<?xml", "SVG or XML"),
    (b"<svg", "SVG"),
)


def sniff_image_mime(raw: bytes) -> str:
    """Return the mime type of `raw`, or raise ValidationError if unusable.

    Only the formats MCP hosts render are accepted. Anything else fails with a
    message naming the format that was found, so the user knows what to convert.
    """
    for signature, mime_type in _SUPPORTED_SIGNATURES:
        if raw.startswith(signature):
            return mime_type
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"

    for signature, label in _UNSUPPORTED_SIGNATURES:
        if raw.startswith(signature):
            raise ValidationError(
                f"Field contains {label} data, which MCP clients cannot display. "
                "Only PNG, JPEG, GIF and WebP can be shown."
            )
    raise ValidationError(
        "Field does not contain a recognizable image (expected PNG, JPEG, GIF or WebP)"
    )


def _human_size(num_bytes: int) -> str:
    """Byte count for the summary line, readable at both ends of the range."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes // 1024} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


class ImageToolsMixin:
    """get_image tool."""

    def _register_image_tools(self):
        """Register image read tool handlers with FastMCP."""

        @self.app.tool(
            title="Get Image (Show a Record's Picture)",
            # Without this, FastMCP derives an output schema from the return
            # annotation and ships the same base64 twice: once as content, once
            # as structuredContent.
            structured_output=False,
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def get_image(
            model: str,
            record_id: int,
            field_name: Optional[str] = None,
            size: int = DEFAULT_IMAGE_SIZE,
            connection: Optional[str] = None,
        ) -> List[ContentBlock]:
            """Show the image stored on a record: product photo, avatar, logo.

            Use this when the user wants to SEE something ("show me that
            product", "what does this contact's avatar look like"). For reading
            data, keep using get_record and search_records - they leave image
            fields out on purpose.

            Images cost tokens on every turn they stay in the conversation, so
            ask for the smallest size that answers the question. 512 is the
            default and is enough to recognize a product; 1920 is print quality
            and rarely needed.

            Only PNG, JPEG, GIF and WebP can be displayed. Non-image binaries
            (PDF invoices, spreadsheets) are not supported by this tool.

            Args:
                model: Odoo model name (e.g. 'product.template', 'res.partner')
                record_id: ID of the record to read
                field_name: Optional. Specific image field, e.g. 'image_1920' or
                    'image_variant_1920'. Leave empty to let the server pick the
                    variant matching `size`.
                size: Pixel variant to prefer: 128, 256, 512, 1024 or 1920.
                    Ignored when field_name is given.
                connection: Optional. Target a specific Odoo connection by the id
                    from server_info's `connections` list. Hosted multi-tenant
                    only; ignored when self-hosting a single connection.

            Returns:
                The picture, plus one line naming the field it came from.
            """
            info = await self._handle_get_image_tool(model, record_id, field_name, size, connection)
            self._track_usage(_current_sub.get(), "get_image")
            summary = (
                f"{info['model']}({info['record_id']}).{info['field']}: "
                f"{info['mime_type']}, {_human_size(info['size_bytes'])}. {info['url']}"
            )
            return [
                TextContent(type="text", text=summary),
                ImageContent(type="image", data=info["data"], mimeType=info["mime_type"]),
            ]

    async def _handle_get_image_tool(
        self,
        model: str,
        record_id: int,
        field_name: Optional[str] = None,
        size: int = DEFAULT_IMAGE_SIZE,
        connection_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle get_image tool request.

        Resolves which image field to read, reads it through the normal ACL
        path, and returns the decoded bytes re-encoded as clean base64 together
        with the sniffed mime type.
        """
        try:
            connection, access_controller, _sub = await self._get_user_context(connection_selector)
            with perf_logger.track_operation("tool_get_image", model=model):
                await validate_access(connection, access_controller, model, "read")
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")
                if size not in IMAGE_SIZES:
                    raise ValidationError(
                        f"size must be one of {', '.join(str(s) for s in IMAGE_SIZES)}, got {size}"
                    )

                fields_info = await run_blocking(connection, connection.fields_get, model)
                if not isinstance(fields_info, dict):
                    raise ValidationError(f"Could not introspect fields of {model}")

                target_field = _resolve_image_field(model, fields_info, field_name, size)

                records = await run_blocking(
                    connection, connection.read, model, [record_id], [target_field]
                )
                if not records:
                    raise NotFoundError(f"Record not found: {model} with ID {record_id}")

                raw = _decode_image_value(
                    records[0].get(target_field), model, record_id, target_field
                )
                if len(raw) > MAX_IMAGE_BYTES:
                    raise ValidationError(
                        f"Image is {len(raw) // (1024 * 1024)} MB, over the "
                        f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB limit. Ask for a smaller "
                        f"size (e.g. size=256)."
                    )
                mime_type = sniff_image_mime(raw)

                base_url = (
                    getattr(connection, "_base_url", None)
                    or (self.config.url if self.config else "")
                ).rstrip("/")
                return {
                    "model": model,
                    "record_id": record_id,
                    "field": target_field,
                    "mime_type": mime_type,
                    "size_bytes": len(raw),
                    "data": base64.b64encode(raw).decode("ascii"),
                    "url": f"{base_url}/web#id={record_id}&model={model}&view_type=form",
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
            logger.error(f"Error in get_image tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to read image: {sanitized_msg}") from e


def _resolve_image_field(
    model: str,
    fields_info: Dict[str, Any],
    field_name: Optional[str],
    size: int,
) -> str:
    """Return the field to read, or raise ValidationError explaining why not.

    An explicit field_name is used as given. Without one, the variant matching
    `size` wins, then the remaining variants, then a bare `image` field.
    """

    def is_binary(name: str) -> bool:
        return fields_info.get(name, {}).get("type") in ("binary", "image")

    if field_name:
        if field_name not in fields_info:
            raise ValidationError(f"Field '{field_name}' does not exist on model '{model}'")
        if not is_binary(field_name):
            ftype = fields_info[field_name].get("type")
            raise ValidationError(f"Field '{field_name}' is type '{ftype}', not binary/image")
        return field_name

    candidates = [f"image_{size}"] + [f for f in _FALLBACK_FIELDS if f != f"image_{size}"]
    for candidate in candidates:
        if is_binary(candidate):
            return candidate

    available = sorted(name for name in fields_info if fields_info[name].get("type") == "image")
    if available:
        raise ValidationError(
            f"Model '{model}' has no standard image_* variant. Pass field_name explicitly; "
            f"image fields on this model: {', '.join(available[:10])}"
        )
    raise ValidationError(f"Model '{model}' has no image fields")


def _decode_image_value(value: Any, model: str, record_id: int, field: str) -> bytes:
    """Decode Odoo's base64 field value into bytes.

    Odoo returns False for an empty binary field, a base64 str over both
    XML-RPC and JSON/2, and occasionally bytes.
    """
    if not value:
        raise ValidationError(f"{model}({record_id}).{field} is empty: no image stored")
    if isinstance(value, bytes):
        payload: Any = value
    elif isinstance(value, str):
        payload = value.encode("ascii", errors="ignore")
    else:
        raise ValidationError(
            f"{model}({record_id}).{field} returned {type(value).__name__}, expected base64 data"
        )
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as e:
        raise ValidationError(f"{model}({record_id}).{field} is not valid base64: {e}") from e
