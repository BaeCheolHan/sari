"""
Centralized logging utilities for Sari project.

This module provides utilities to eliminate duplicate logging patterns
and provide consistent logging across all modules.
"""

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class LoggerProtocol(Protocol):
    def info(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def debug(self, message: str) -> None: ...


@runtime_checkable
class TelemetryLoggerProtocol(Protocol):
    def log_info(self, message: str) -> None: ...
    def log_error(self, message: str) -> None: ...
    def log_warning(self, message: str) -> None: ...
    def log_debug(self, message: str) -> None: ...


def safe_log(
    logger: Optional[object],
    level: str,
    message: str,
) -> None:
    """
    Safely log a message with fallback support for different logger types.
    
    This function handles the common pattern of checking if a logger exists
    and calling the appropriate logging method based on the logger type.
    
    Supports:
    - TelemetryLogger (log_info/log_error/log_warning/log_debug)
    - stdlib logging.Logger (info/error/warning/debug)
    - Generic logger with log() method
    
    Args:
        logger: Logger instance (can be None)
        level: Log level ('info', 'error', 'warning', 'debug')
        message: Message to log
    
    Examples:
        >>> safe_log(logger, "info", "Processing started")
        >>> safe_log(logger, "error", f"Failed to process {file}: {error}")
        >>> safe_log(None, "info", "This will be silently ignored")
    """
    if not logger:
        return
    
    # Try TelemetryLogger style (log_info, log_error, etc.)
    method = getattr(logger, f"log_{level}", None)
    if callable(method):
        method(message)
        return
    
    # Try stdlib logging style (info, error, etc.)
    method = getattr(logger, level, None)
    if callable(method):
        method(message)
        return
    
    # Fallback to generic log method
    method = getattr(logger, "log", None)
    if callable(method):
        method(message)


class LoggerMixin:
    """
    Mixin to add safe logging methods to any class.
    
    This mixin provides convenient logging methods that automatically
    use the class's logger attribute if available.
    
    Usage:
        class MyClass(LoggerMixin):
            def __init__(self, logger=None):
                self.logger = logger
            
            def process(self):
                self.log_info("Starting processing")
                try:
                    # ... work ...
                    self.log_info("Processing complete")
                except Exception as e:
                    self.log_error(f"Processing failed: {e}")
    
    Attributes:
        logger: Optional logger instance (must be set by the class)
    """
    
    logger: Optional[object] = None
    
    def log_info(self, message: str) -> None:
        """Log an info message."""
        safe_log(self.logger, "info", message)
    
    def log_error(self, message: str) -> None:
        """Log an error message."""
        safe_log(self.logger, "error", message)
    
    def log_warning(self, message: str) -> None:
        """Log a warning message."""
        safe_log(self.logger, "warning", message)
    
    def log_debug(self, message: str) -> None:
        """Log a debug message."""
        safe_log(self.logger, "debug", message)


def create_error_context(operation: str, error: Exception, **context) -> str:
    """
    Create a formatted error message with context.
    
    This utility helps create consistent error messages across the codebase.
    
    Args:
        operation: Description of the operation that failed
        error: The exception that occurred
        **context: Additional context key-value pairs
    
    Returns:
        Formatted error message string
    
    Examples:
        >>> create_error_context("file processing", FileNotFoundError("test.txt"), path="/tmp/test.txt")
        'file processing failed: test.txt (path=/tmp/test.txt)'
        
        >>> create_error_context("database query", ValueError("Invalid ID"), query="SELECT * FROM users", id=123)
        'database query failed: Invalid ID (query=SELECT * FROM users, id=123)'
    """
    parts = [f"{operation} failed: {error}"]
    
    if context:
        ctx_str = ", ".join(f"{k}={v}" for k, v in context.items())
        parts.append(f"({ctx_str})")
    
    return " ".join(parts)


class LogContext:
    """
    Context manager for logging operation start/end with automatic error handling.
    
    This provides a convenient way to log the lifecycle of an operation,
    including automatic error logging if an exception occurs.
    
    Usage:
        with LogContext(logger, "processing file", path=file_path):
            # ... do work ...
            pass
        
        # Logs:
        # INFO: processing file started (path=/tmp/file.txt)
        # INFO: processing file completed (path=/tmp/file.txt)
        
        # Or on error:
        # INFO: processing file started (path=/tmp/file.txt)
        # ERROR: processing file failed: FileNotFoundError (path=/tmp/file.txt)
    """
    
    def __init__(
        self,
        logger: Optional[object],
        operation: str,
        log_start: bool = True,
        log_end: bool = True,
        **context
    ):
        """
        Initialize log context.
        
        Args:
            logger: Logger instance
            operation: Description of the operation
            log_start: Whether to log when entering context
            log_end: Whether to log when exiting context successfully
            **context: Additional context to include in log messages
        """
        self.logger = logger
        self.operation = operation
        self.log_start = log_start
        self.log_end = log_end
        self.context = context
    
    def _format_message(self, status: str) -> str:
        """Format log message with context."""
        msg = f"{self.operation} {status}"
        if self.context:
            ctx_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            msg += f" ({ctx_str})"
        return msg
    
    def __enter__(self):
        """Enter context and log start."""
        if self.log_start:
            safe_log(self.logger, "info", self._format_message("started"))
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context and log completion or error."""
        if exc_type is not None:
            # Exception occurred
            error_msg = create_error_context(self.operation, exc_val, **self.context)
            safe_log(self.logger, "error", error_msg)
            return False  # Re-raise exception
        
        if self.log_end:
            safe_log(self.logger, "info", self._format_message("completed"))
        return True
