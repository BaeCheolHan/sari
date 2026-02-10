"""
Tests for logging utilities module.
"""

import pytest
from sari.core.logging_utils import (
    safe_log,
    LoggerMixin,
    create_error_context,
    LogContext,
)


class MockLogger:
    """Mock logger for testing."""
    
    def __init__(self):
        self.messages = []
    
    def log_info(self, msg):
        self.messages.append(("info", msg))
    
    def log_error(self, msg):
        self.messages.append(("error", msg))
    
    def log_warning(self, msg):
        self.messages.append(("warning", msg))
    
    def log_debug(self, msg):
        self.messages.append(("debug", msg))


class StdlibStyleLogger:
    """Mock stdlib-style logger."""
    
    def __init__(self):
        self.messages = []
    
    def info(self, msg):
        self.messages.append(("info", msg))
    
    def error(self, msg):
        self.messages.append(("error", msg))
    
    def warning(self, msg):
        self.messages.append(("warning", msg))
    
    def debug(self, msg):
        self.messages.append(("debug", msg))


def test_safe_log_with_telemetry_logger():
    """Test safe_log with TelemetryLogger-style logger."""
    logger = MockLogger()
    
    safe_log(logger, "info", "Test info message")
    safe_log(logger, "error", "Test error message")
    safe_log(logger, "warning", "Test warning message")
    
    assert len(logger.messages) == 3
    assert logger.messages[0] == ("info", "Test info message")
    assert logger.messages[1] == ("error", "Test error message")
    assert logger.messages[2] == ("warning", "Test warning message")


def test_safe_log_with_stdlib_logger():
    """Test safe_log with stdlib-style logger."""
    logger = StdlibStyleLogger()
    
    safe_log(logger, "info", "Test info")
    safe_log(logger, "error", "Test error")
    
    assert len(logger.messages) == 2
    assert logger.messages[0] == ("info", "Test info")
    assert logger.messages[1] == ("error", "Test error")


def test_safe_log_with_none_logger():
    """Test safe_log with None logger (should not crash)."""
    result = safe_log(None, "info", "This should be silently ignored")
    assert result is None


def test_logger_mixin():
    """Test LoggerMixin class."""
    
    class TestClass(LoggerMixin):
        def __init__(self, logger):
            self.logger = logger
    
    logger = MockLogger()
    obj = TestClass(logger)
    
    obj.log_info("Info message")
    obj.log_error("Error message")
    obj.log_warning("Warning message")
    obj.log_debug("Debug message")
    
    assert len(logger.messages) == 4
    assert logger.messages[0] == ("info", "Info message")
    assert logger.messages[1] == ("error", "Error message")
    assert logger.messages[2] == ("warning", "Warning message")
    assert logger.messages[3] == ("debug", "Debug message")


def test_logger_mixin_without_logger():
    """Test LoggerMixin without a logger (should not crash)."""
    
    class TestClass(LoggerMixin):
        pass
    
    obj = TestClass()
    result = obj.log_info("This should be silently ignored")
    assert result is None


def test_create_error_context():
    """Test create_error_context function."""
    error = ValueError("Invalid input")
    
    # Without context
    msg = create_error_context("processing", error)
    assert msg == "processing failed: Invalid input"
    
    # With context
    msg = create_error_context("processing", error, file="test.txt", line=42)
    assert "processing failed: Invalid input" in msg
    assert "file=test.txt" in msg
    assert "line=42" in msg


def test_log_context_success():
    """Test LogContext for successful operation."""
    logger = MockLogger()
    
    with LogContext(logger, "test operation", key="value"):
        pass
    
    assert len(logger.messages) == 2
    assert logger.messages[0] == ("info", "test operation started (key=value)")
    assert logger.messages[1] == ("info", "test operation completed (key=value)")


def test_log_context_with_error():
    """Test LogContext when an error occurs."""
    logger = MockLogger()
    
    with pytest.raises(ValueError):
        with LogContext(logger, "test operation", key="value"):
            raise ValueError("Test error")
    
    assert len(logger.messages) == 2
    assert logger.messages[0] == ("info", "test operation started (key=value)")
    assert "test operation failed: Test error" in logger.messages[1][1]
    assert "key=value" in logger.messages[1][1]


def test_log_context_no_start_log():
    """Test LogContext with log_start=False."""
    logger = MockLogger()
    
    with LogContext(logger, "test operation", log_start=False):
        pass
    
    assert len(logger.messages) == 1
    assert logger.messages[0][0] == "info"
    assert "completed" in logger.messages[0][1]


def test_log_context_no_end_log():
    """Test LogContext with log_end=False."""
    logger = MockLogger()
    
    with LogContext(logger, "test operation", log_end=False):
        pass
    
    assert len(logger.messages) == 1
    assert logger.messages[0][0] == "info"
    assert "started" in logger.messages[0][1]


def test_log_context_with_none_logger():
    """Test LogContext with None logger (should not crash)."""
    with LogContext(None, "test operation"):
        marker = True
    assert marker is True
