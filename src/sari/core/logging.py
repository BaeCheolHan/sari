# logging.py
import structlog
from structlog.stdlib import BoundLogger

def setup_logging() -> None:
    """애플리케이션을 위한 구조화된 로깅을 설정합니다."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.set_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

# 초기 설정
setup_logging()
log: BoundLogger = structlog.get_logger()

def get_logger(name: str) -> BoundLogger:
    """지정된 모듈 이름에 대한 로거 인스턴스를 반환합니다."""
    return structlog.get_logger(name)
