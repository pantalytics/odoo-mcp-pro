"""Tests for the get_image tool."""

import base64
from unittest.mock import Mock

import pytest

from mcp_server_odoo.access_control import AccessControlError
from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.tools import MAX_IMAGE_BYTES, OdooToolHandler
from mcp_server_odoo.tools.images import sniff_image_mime

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16

IMAGE_FIELDS = {
    "name": {"type": "char"},
    "image_128": {"type": "image"},
    "image_256": {"type": "image"},
    "image_512": {"type": "image"},
    "image_1920": {"type": "image"},
    "notes": {"type": "text"},
}


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class TestSniffImageMime:
    """Format detection works off the bytes, not the field name."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (PNG_BYTES, "image/png"),
            (JPEG_BYTES, "image/jpeg"),
            (b"GIF89a" + b"\x00" * 16, "image/gif"),
            (WEBP_BYTES, "image/webp"),
        ],
    )
    def test_supported_formats(self, raw, expected):
        assert sniff_image_mime(raw) == expected

    def test_known_but_unrenderable_format_names_itself(self):
        with pytest.raises(ValidationError, match="PDF"):
            sniff_image_mime(b"%PDF-1.7\n%rest")

    def test_unknown_bytes_are_refused(self):
        with pytest.raises(ValidationError, match="recognizable image"):
            sniff_image_mime(b"just some text, not an image at all")


class TestGetImage:
    """Test the get_image tool handler."""

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
        conn.fields_get.return_value = IMAGE_FIELDS
        conn.read.return_value = [{"id": 7, "image_512": b64(PNG_BYTES)}]
        return conn

    @pytest.fixture
    def mock_access_controller(self):
        controller = Mock()
        controller.validate_model_access = Mock()
        return controller

    @pytest.fixture
    def mock_config(self):
        config = Mock()
        config.url = "http://localhost:8069"
        return config

    @pytest.fixture
    def handler(self, mock_app, mock_connection, mock_access_controller, mock_config):
        return OdooToolHandler(mock_app, mock_connection, mock_access_controller, mock_config)

    @pytest.mark.asyncio
    async def test_default_size_picks_matching_variant(self, handler, mock_connection):
        """No field_name: the variant matching `size` is read."""
        result = await handler._handle_get_image_tool("product.template", 7)

        assert result["field"] == "image_512"
        assert result["mime_type"] == "image/png"
        assert result["size_bytes"] == len(PNG_BYTES)
        assert base64.b64decode(result["data"]) == PNG_BYTES
        assert result["url"] == (
            "http://localhost:8069/web#id=7&model=product.template&view_type=form"
        )
        mock_connection.read.assert_called_once_with("product.template", [7], ["image_512"])

    @pytest.mark.asyncio
    async def test_requested_size_maps_to_variant_field(self, handler, mock_connection):
        mock_connection.read.return_value = [{"id": 7, "image_128": b64(JPEG_BYTES)}]

        result = await handler._handle_get_image_tool("product.template", 7, size=128)

        assert result["field"] == "image_128"
        assert result["mime_type"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_missing_variant_falls_back_to_another_one(self, handler, mock_connection):
        """A model without image_1024 still answers, from the next variant."""
        mock_connection.fields_get.return_value = {"image_1920": {"type": "image"}}
        mock_connection.read.return_value = [{"id": 7, "image_1920": b64(PNG_BYTES)}]

        result = await handler._handle_get_image_tool("res.partner", 7, size=1024)

        assert result["field"] == "image_1920"

    @pytest.mark.asyncio
    async def test_explicit_field_is_used_verbatim(self, handler, mock_connection):
        mock_connection.fields_get.return_value = {
            **IMAGE_FIELDS,
            "image_variant_1920": {"type": "image"},
        }
        mock_connection.read.return_value = [{"id": 7, "image_variant_1920": b64(PNG_BYTES)}]

        result = await handler._handle_get_image_tool(
            "product.product", 7, field_name="image_variant_1920", size=128
        )

        assert result["field"] == "image_variant_1920"
        mock_connection.read.assert_called_once_with("product.product", [7], ["image_variant_1920"])

    @pytest.mark.asyncio
    async def test_non_binary_field_is_refused(self, handler):
        with pytest.raises(ValidationError, match="not binary/image"):
            await handler._handle_get_image_tool("product.template", 7, field_name="name")

    @pytest.mark.asyncio
    async def test_unknown_field_is_refused(self, handler):
        with pytest.raises(ValidationError, match="does not exist"):
            await handler._handle_get_image_tool("product.template", 7, field_name="nope")

    @pytest.mark.asyncio
    async def test_model_without_image_fields(self, handler, mock_connection):
        mock_connection.fields_get.return_value = {"name": {"type": "char"}}

        with pytest.raises(ValidationError, match="no image fields"):
            await handler._handle_get_image_tool("account.move", 7)

    @pytest.mark.asyncio
    async def test_model_with_only_custom_image_field_lists_it(self, handler, mock_connection):
        mock_connection.fields_get.return_value = {"x_photo": {"type": "image"}}

        with pytest.raises(ValidationError, match="x_photo"):
            await handler._handle_get_image_tool("x.model", 7)

    @pytest.mark.asyncio
    async def test_binary_typed_image_fields_still_resolve(self, handler, mock_connection):
        """odoo.com reports image_128 as type 'binary', self-hosted as 'image'."""
        mock_connection.fields_get.return_value = {
            "name": {"type": "char"},
            "image_512": {"type": "binary"},
        }

        result = await handler._handle_get_image_tool("product.template", 7)

        assert result["field"] == "image_512"

    @pytest.mark.asyncio
    async def test_binary_field_is_named_in_the_hint(self, handler, mock_connection):
        """A model with binary fields but no image_* variant says which to try."""
        mock_connection.fields_get.return_value = {
            "name": {"type": "char"},
            "datas": {"type": "binary"},
        }

        with pytest.raises(ValidationError, match="binary/image fields on this model: datas"):
            await handler._handle_get_image_tool("ir.attachment", 7)

    @pytest.mark.asyncio
    async def test_invalid_size_is_refused(self, handler):
        with pytest.raises(ValidationError, match="size must be one of"):
            await handler._handle_get_image_tool("product.template", 7, size=300)

    @pytest.mark.asyncio
    async def test_empty_field_says_so(self, handler, mock_connection):
        mock_connection.read.return_value = [{"id": 7, "image_512": False}]

        with pytest.raises(ValidationError, match="no image stored"):
            await handler._handle_get_image_tool("product.template", 7)

    @pytest.mark.asyncio
    async def test_missing_record(self, handler, mock_connection):
        mock_connection.read.return_value = []

        with pytest.raises(ValidationError, match="Record not found"):
            await handler._handle_get_image_tool("product.template", 999)

    @pytest.mark.asyncio
    async def test_oversized_image_suggests_smaller_size(self, handler, mock_connection):
        oversized = PNG_BYTES + b"\x00" * (MAX_IMAGE_BYTES + 1)
        mock_connection.read.return_value = [{"id": 7, "image_512": b64(oversized)}]

        with pytest.raises(ValidationError, match="size=256"):
            await handler._handle_get_image_tool("product.template", 7)

    @pytest.mark.asyncio
    async def test_pdf_in_binary_field_is_refused(self, handler, mock_connection):
        mock_connection.fields_get.return_value = {"datas": {"type": "binary"}}
        mock_connection.read.return_value = [{"id": 7, "datas": b64(b"%PDF-1.7 fake")}]

        with pytest.raises(ValidationError, match="PDF"):
            await handler._handle_get_image_tool("ir.attachment", 7, field_name="datas")

    @pytest.mark.asyncio
    async def test_bytes_value_is_accepted(self, handler, mock_connection):
        """Some transports hand back bytes instead of str."""
        mock_connection.read.return_value = [{"id": 7, "image_512": b64(PNG_BYTES).encode()}]

        result = await handler._handle_get_image_tool("product.template", 7)

        assert result["mime_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_not_authenticated(self, handler, mock_connection):
        mock_connection.is_authenticated = False

        with pytest.raises(ValidationError, match="Not authenticated"):
            await handler._handle_get_image_tool("product.template", 7)

    @pytest.mark.asyncio
    async def test_access_denied_is_reported(self, handler, mock_access_controller):
        mock_access_controller.validate_model_access.side_effect = AccessControlError("nope")

        with pytest.raises(ValidationError, match="Access denied"):
            await handler._handle_get_image_tool("product.template", 7)

    @pytest.mark.asyncio
    async def test_read_only_annotation(self, handler, mock_app):
        """The tool must be registered read-only: it never writes to Odoo."""
        calls = [c.kwargs for c in mock_app.tool.call_args_list if "Get Image" in str(c.kwargs)]
        assert calls, "get_image was not registered"
        assert calls[0]["annotations"].readOnlyHint is True
        # Structured output off, or the base64 ships twice (content + structured).
        assert calls[0]["structured_output"] is False


class TestGetImageOverFastMCP:
    """The tool must behave through real FastMCP, not just the handler.

    FastMCP derives an output schema from the return annotation unless
    structured_output is off. With a schema, the same base64 would be sent
    twice (content + structuredContent), doubling what the client downloads.
    """

    @pytest.fixture
    def app(self):
        from mcp.server.fastmcp import FastMCP

        from mcp_server_odoo.tools import register_tools

        conn = Mock()
        conn.is_authenticated = True
        conn._base_url = "http://localhost:8069"
        conn.fields_get.return_value = {"image_512": {"type": "image"}}
        conn.read.return_value = [{"id": 7, "image_512": b64(PNG_BYTES)}]
        access = Mock()
        access.validate_model_access = Mock()
        config = Mock()
        config.url = "http://localhost:8069"

        app = FastMCP("test")
        register_tools(app, conn, access, config)
        return app

    @pytest.mark.asyncio
    async def test_tool_is_listed_without_output_schema(self, app):
        tools = {tool.name: tool for tool in await app.list_tools()}

        assert "get_image" in tools
        assert tools["get_image"].outputSchema is None
        assert tools["get_image"].annotations.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_call_returns_text_plus_image(self, app):
        from mcp.types import ImageContent, TextContent

        result = await app.call_tool("get_image", {"model": "product.template", "record_id": 7})
        content, structured = result if isinstance(result, tuple) else (result, None)

        assert structured is None
        assert isinstance(content[0], TextContent)
        assert "image_512" in content[0].text
        assert isinstance(content[1], ImageContent)
        assert content[1].mimeType == "image/png"
        assert base64.b64decode(content[1].data) == PNG_BYTES
