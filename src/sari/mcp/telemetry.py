import sys
from pathlib import Path
from typing import Optional
import structlog
from sari.core.utils.logging import get_logger

try:
    from sari.core.indexer import _redact
except ImportError:
    def _redact(t): return t

class TelemetryLogger:
    """
    Handles logging and telemetry for MCP server.
    Refactored to use structlog (Phase 4).
    """

    def __init__(self, log_dir: Optional[Path] = None):
        """
        Initialize telemetry logger.
        
        Args:
            log_dir: Deprecated, kept for backward compatibility.
                     Logging destination is configured globally via structlog.
        """
        self.logger = get_logger("sari.mcp.telemetry")

    def log_error(self, message: str) -> None:
        """Log error message."""
        self.logger.error("error_logged", message=message)

    def log_info(self, message: str) -> None:
        """Log info message."""
        self.logger.info("info_logged", message=message)

    def log_telemetry(self, message: str) -> None:
        """Log telemetry message."""
        # Map telemetry to info or a specific event
        self.logger.info("telemetry_event", payload=message)

    def stop(self, timeout: float = 2.0) -> None:
        """No-op for structlog adapter."""
        pass

    def get_queue_depth(self) -> int:
        return 0

    def get_drop_count(self) -> int:
        return 0