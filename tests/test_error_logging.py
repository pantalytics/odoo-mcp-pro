"""Tests for logging configuration and global error/logging instances."""

import json
import logging
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from mcp_server_odoo.error_handling import (
    ValidationError,
    error_handler,
)
from mcp_server_odoo.logging_config import (
    LoggingConfig,
    PerformanceLogger,
    RequestLoggingAdapter,
    StructuredFormatter,
    log_request,
    log_response,
    logging_config,
    perf_logger,
    setup_logging,
)


class TestLoggingConfiguration:
    """Test logging configuration and utilities."""

    def test_structured_formatter(self):
        """Test JSON log formatting."""
        formatter = StructuredFormatter()

        # Create a log record
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Add extra fields
        record.error_code = "TEST_ERROR"
        record.model = "res.partner"

        formatted = formatter.format(record)
        log_data = json.loads(formatted)

        assert log_data["logger"] == "test.logger"
        assert log_data["level"] == "INFO"
        assert log_data["message"] == "Test message"
        assert log_data["error_code"] == "TEST_ERROR"
        assert log_data["model"] == "res.partner"
        assert "timestamp" in log_data

    def test_request_logging_adapter(self):
        """Test request logging adapter."""
        logger = logging.getLogger("test")
        adapter = RequestLoggingAdapter(logger, request_id="test-123")

        assert adapter.request_id == "test-123"

        # Test that request ID is added to extra
        msg, kwargs = adapter.process("Test message", {})
        assert kwargs["extra"]["request_id"] == "test-123"

    def test_performance_logger(self):
        """Test performance tracking."""
        logger = MagicMock()
        perf = PerformanceLogger(logger)

        with perf.track_operation("test_op", model="res.partner"):
            time.sleep(0.01)  # Small delay

        # Check that info was logged
        logger.info.assert_called()
        call_args = logger.info.call_args
        assert "test_op" in call_args[0][0]
        assert "completed in" in call_args[0][0]
        assert call_args[1]["extra"]["operation"] == "test_op"
        assert call_args[1]["extra"]["model"] == "res.partner"
        assert call_args[1]["extra"]["duration_ms"] > 0

    def test_setup_logging(self):
        """Test logging setup."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            setup_logging(
                log_level="DEBUG",
                use_json=True,
                log_file=tmp.name,
            )

            logger = logging.getLogger("test")
            logger.debug("Test debug message")

            # Check that file was written
            assert os.path.exists(tmp.name)
            assert os.path.getsize(tmp.name) > 0

            # Clean up
            os.unlink(tmp.name)

    def test_logging_config_from_env(self):
        """Test loading logging config from environment."""
        with patch.dict(
            os.environ,
            {
                "ODOO_MCP_LOG_LEVEL": "DEBUG",
                "ODOO_MCP_LOG_JSON": "true",
                "ODOO_MCP_LOG_FILE": "/tmp/test.log",
                "ODOO_MCP_SLOW_OPERATION_THRESHOLD_MS": "500",
            },
        ):
            config = LoggingConfig()

            assert config.log_level == "DEBUG"
            assert config.use_json is True
            assert config.log_file == "/tmp/test.log"
            assert config.slow_operation_threshold_ms == 500

    def test_log_request_response(self):
        """Test request/response logging helpers."""
        logger = MagicMock()

        # Test request logging
        log_request(
            logger,
            method="GET",
            path="/api/test",
            params={"limit": 10},
            body={"filter": "active"},
        )

        logger.info.assert_called()
        call_args = logger.info.call_args
        assert "GET /api/test" in call_args[0][0]
        assert call_args[1]["extra"]["request_method"] == "GET"
        assert call_args[1]["extra"]["request_params"] == {"limit": 10}

        # Test response logging
        log_response(
            logger,
            status="200 OK",
            duration_ms=123.45,
            response_size=1024,
        )

        assert logger.info.call_count == 2
        call_args = logger.info.call_args
        assert "200 OK (123.45ms)" in call_args[0][0]
        assert call_args[1]["extra"]["response_status"] == "200 OK"
        assert call_args[1]["extra"]["response_size"] == 1024

        # Test error response logging
        log_response(
            logger,
            status="500 Error",
            duration_ms=50.0,
            error="Internal server error",
        )

        logger.error.assert_called()
        call_args = logger.error.call_args
        assert "500 Error" in call_args[0][0]
        assert "Internal server error" in call_args[0][0]


class TestGlobalInstances:
    """Test global error handler and logging instances."""

    def test_global_error_handler(self):
        """Test that global error handler works correctly."""
        # Clear any existing state
        error_handler.clear_metrics()

        # Generate an error
        with pytest.raises(ValidationError):
            error_handler.handle_error(ValueError("Test"))

        # Check metrics
        metrics = error_handler.get_metrics()
        assert metrics["total_errors"] == 1

    def test_global_perf_logger(self):
        """Test that global performance logger works."""
        with perf_logger.track_operation("test_operation"):
            time.sleep(0.01)

        # Operation should complete without error

    def test_global_logging_config(self):
        """Test that global logging config works."""
        assert isinstance(logging_config, LoggingConfig)
        assert hasattr(logging_config, "log_level")
        assert hasattr(logging_config, "setup")
