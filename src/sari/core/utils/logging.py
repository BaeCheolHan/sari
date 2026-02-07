import os
import sys
import logging
import structlog
from sari.core.settings import settings

def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)

def configure_logging():
    """Configure structured logging."""
    log_level = os.environ.get("SARI_LOG_LEVEL", "INFO").upper()
    json_logs = os.environ.get("SARI_LOG_JSON", "1") == "1"

    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, log_level, logging.INFO),
    )

    processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_logs:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
