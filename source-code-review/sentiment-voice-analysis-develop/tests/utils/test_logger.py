"""
Tests for logger module.
"""

import logging
import os
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.utils.logger import CustomJsonFormatter, Logger


class TestCustomJsonFormatter:
    """Test suite for CustomJsonFormatter class."""

    def test_init_default_values(self):
        """Test CustomJsonFormatter initialization with default values."""
        formatter = CustomJsonFormatter()
        assert formatter.project_name == "sentiment-batch"
        assert formatter.team_name == "RPA&AI-Automation"

    def test_init_custom_values(self):
        """Test CustomJsonFormatter initialization with custom values."""
        formatter = CustomJsonFormatter(project_name="test-project", team_name="test-team")
        assert formatter.project_name == "test-project"
        assert formatter.team_name == "test-team"

    def test_add_fields_basic_structure(self):
        """Test that add_fields creates proper log structure."""
        formatter = CustomJsonFormatter()
        log_record = {}
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py", lineno=1, msg="Test message", args=(), exc_info=None
        )

        formatter.add_fields(log_record, record, {})

        assert "severity" in log_record
        assert "project_name" in log_record
        assert "team" in log_record
        assert "datetime" in log_record
        assert "logger_name" in log_record
        assert "message" in log_record

    def test_add_fields_severity_mapping(self):
        """Test that severity is correctly set."""
        formatter = CustomJsonFormatter()
        log_record = {}
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="test.py", lineno=1, msg="Error message", args=(), exc_info=None
        )

        formatter.add_fields(log_record, record, {})
        assert log_record["severity"] == "ERROR"


class TestLogger:
    """Test suite for Logger class."""

    def test_init_default_environment(self):
        """Test Logger initialization with default environment."""
        with patch.dict(os.environ, {}, clear=True):
            logger = Logger("test_logger")
            assert logger.logger_name == "test_logger"
            assert logger.env == "prod"

    def test_init_nprd_environment(self):
        """Test Logger initialization with nprd environment."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd"}):
            logger = Logger("test_logger")
            assert logger.env == "nprd"

    def test_init_explicit_environment(self):
        """Test Logger initialization with explicit environment parameter."""
        logger = Logger("test_logger", env="nprd")
        assert logger.env == "nprd"

    def test_init_log_level_from_env(self):
        """Test Logger initialization with log level from environment."""
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
            logger = Logger("test_logger")
            assert logger.log_level == logging.DEBUG

    def test_init_invalid_log_level_defaults_to_info(self):
        """Test that invalid log level defaults to INFO."""
        with patch.dict(os.environ, {"LOG_LEVEL": "INVALID"}):
            logger = Logger("test_logger")
            assert logger.log_level == logging.INFO

    def test_info_logging(self, caplog):
        """Test info level logging."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd", "LOG_LEVEL": "INFO"}):
            logger = Logger("test_logger")
            with caplog.at_level(logging.INFO):
                logger.info("Test info message")
                # Verify message was logged (actual content depends on handler config)

    def test_debug_logging(self, caplog):
        """Test debug level logging."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd", "LOG_LEVEL": "DEBUG"}):
            logger = Logger("test_logger")
            with caplog.at_level(logging.DEBUG):
                logger.debug("Test debug message")

    def test_warning_logging(self, caplog):
        """Test warning level logging."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd", "LOG_LEVEL": "WARNING"}):
            logger = Logger("test_logger")
            with caplog.at_level(logging.WARNING):
                logger.warning("Test warning message")

    def test_error_logging(self, caplog):
        """Test error level logging."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd", "LOG_LEVEL": "ERROR"}):
            logger = Logger("test_logger")
            with caplog.at_level(logging.ERROR):
                logger.error("Test error message")

    def test_error_logging_with_exc_info(self, caplog):
        """Test error logging with exception info."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd", "LOG_LEVEL": "ERROR"}):
            logger = Logger("test_logger")
            try:
                raise ValueError("Test exception")
            except ValueError:
                with caplog.at_level(logging.ERROR):
                    logger.error("Error occurred", exc_info=True)

    def test_critical_logging(self, caplog):
        """Test critical level logging."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd", "LOG_LEVEL": "CRITICAL"}):
            logger = Logger("test_logger")
            with caplog.at_level(logging.CRITICAL):
                logger.critical("Test critical message")

    def test_exception_logging(self, caplog):
        """Test exception logging convenience method."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd"}):
            logger = Logger("test_logger")
            try:
                raise RuntimeError("Test exception")
            except RuntimeError:
                with caplog.at_level(logging.ERROR):
                    logger.exception("Exception occurred")

    def test_set_level_valid(self):
        """Test setting log level dynamically."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd"}):
            logger = Logger("test_logger")
            logger.set_level("DEBUG")
            assert logger.log_level == logging.DEBUG

    def test_set_level_invalid_raises_error(self):
        """Test that invalid log level raises ValueError."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd"}):
            logger = Logger("test_logger")
            with pytest.raises(ValueError, match="Invalid log level"):
                logger.set_level("INVALID_LEVEL")

    def test_set_level_case_insensitive(self):
        """Test that set_level is case insensitive."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd"}):
            logger = Logger("test_logger")
            logger.set_level("debug")
            assert logger.log_level == logging.DEBUG

    def test_prod_environment_uses_json_format(self):
        """Test that prod environment uses JSON formatter."""
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}):
            logger = Logger("test_logger")
            # Verify handler has CustomJsonFormatter
            handler = logger.logger.handlers[0]
            assert isinstance(handler.formatter, CustomJsonFormatter)

    def test_nprd_environment_uses_json_format(self):
        """Test that nprd environment uses JSON formatter (dev format is only for 'local')."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd"}):
            logger = Logger("test_logger")
            # nprd is not 'local', so it falls through to JSON format
            handler = logger.logger.handlers[0]
            assert isinstance(handler.formatter, CustomJsonFormatter)

    def test_no_duplicate_handlers(self):
        """Test that creating multiple loggers doesn't duplicate handlers."""
        with patch.dict(os.environ, {"ENVIRONMENT": "nprd"}):
            logger1 = Logger("same_name")
            handler_count_1 = len(logger1.logger.handlers)

            logger2 = Logger("same_name")
            handler_count_2 = len(logger2.logger.handlers)

            # Should have same number of handlers (no duplicates)
            assert handler_count_1 == handler_count_2

    def test_log_name_from_env(self):
        """Test that log name is taken from environment in prod mode."""
        with patch.dict(os.environ, {"ENVIRONMENT": "prod", "LOG_NAME": "custom_log"}):
            Logger("test_logger")
            # Logger should use LOG_NAME for prod

    def test_fallback_on_initialization_failure(self):
        """Test that logger falls back to basic logging on failure."""
        with patch("src.utils.logger.CustomJsonFormatter") as mock_formatter:
            mock_formatter.side_effect = Exception("Formatter error")
            # Should not raise, should fall back
            logger = Logger("test_logger")
            assert logger.logger is not None


def clear_logger(logger_name: str) -> None:
    logger = logging.getLogger(logger_name)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


class TestCustomJsonFormatterAdditional:
    def test_uses_data_from_log_record_when_present(self):
        formatter = CustomJsonFormatter()
        log_record = {"data": {"id": 1}}
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)

        formatter.add_fields(log_record, record, {})

        assert log_record["data"] == {"id": 1}

    def test_includes_exception_and_json_payload(self):
        formatter = CustomJsonFormatter(project_name="project-x", team_name="team-y")
        log_record = {}
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed", (), None)
            record.exc_info = os.sys.exc_info()
            record.json_payload = {"status": "bad"}

        formatter.add_fields(log_record, record, {})

        assert log_record["project_name"] == "project-x"
        assert log_record["team"] == "team-y"
        assert log_record["data"] == {"status": "bad"}
        assert "exception" in log_record


class TestLoggerAdditional:
    def test_local_environment_creates_console_and_file_handlers(self, tmp_path):
        name = f"local-{uuid4()}"
        clear_logger(name)

        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}, clear=False):
            logger = Logger(name, env="local", log_dir=str(tmp_path), log_file="test.log")
            logger.info("hello")

        assert logger.env == "local"
        assert len(logger.logger.handlers) == 2
        assert logger.logger.handlers[0].formatter.__class__ is logging.Formatter
        assert (tmp_path / "test.log").exists()

        clear_logger(name)

    def test_create_file_handler_returns_none_on_failure(self, capsys, tmp_path):
        name = f"handler-{uuid4()}"
        clear_logger(name)
        logger = Logger(name, env="local", log_to_file=False, log_dir=str(tmp_path), log_file="test.log")

        with patch("src.utils.logger.RotatingFileHandler", side_effect=OSError("disk full")):
            handler = logger._create_file_handler(logger._dev_format(), 1024, 1)

        assert handler is None
        assert "Failed to create file handler: disk full" in capsys.readouterr().err

        clear_logger(name)

    def test_falls_back_to_basic_logger_when_formatter_creation_fails(self):
        name = f"fallback-{uuid4()}"
        log_name = f"json-{uuid4()}"
        clear_logger(name)
        clear_logger(log_name)

        with patch.dict(os.environ, {"LOG_NAME": log_name}, clear=False):
            with patch.object(Logger, "_json_format", side_effect=Exception("bad formatter")):
                logger = Logger(name, env="prod")

        assert logger.logger.name == name
        assert logger.logger.level == logging.INFO
        assert logger.logger.handlers

        clear_logger(name)
        clear_logger(log_name)

    def test_json_format_uses_environment_values(self):
        name = f"json-format-{uuid4()}"
        clear_logger(name)

        with patch.dict(os.environ, {"PROJECT_NAME": "proj", "TEAM_NAME": "team"}, clear=False):
            logger = Logger(name, env="prod")
            formatter = logger._json_format()

        assert isinstance(formatter, CustomJsonFormatter)
        assert formatter.project_name == "proj"
        assert formatter.team_name == "team"

        clear_logger("app")
        clear_logger(name)

    def test_set_level_logs_change(self):
        name = f"set-level-{uuid4()}"
        clear_logger(name)
        logger = Logger(name, env="local", log_to_file=False)

        with patch.object(logger.logger, "info") as mock_info:
            logger.set_level("warning")

        assert logger.log_level == logging.WARNING
        mock_info.assert_called_once_with("Log level changed to: WARNING")

        clear_logger(name)
