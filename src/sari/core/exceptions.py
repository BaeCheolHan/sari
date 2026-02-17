"""도메인 예외를 정의한다."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorContext:
    """오류 응답에 필요한 표준 정보를 담는다."""

    code: str
    message: str


class SariBaseError(Exception):
    """모든 도메인 예외의 공통 기반 클래스다."""

    def __init__(self, context: ErrorContext) -> None:
        """예외 생성 시 표준 오류 문맥을 고정한다."""
        super().__init__(context.message)
        self.context = context


class WorkspaceError(SariBaseError):
    """워크스페이스 처리 오류를 표현한다."""


class DaemonError(SariBaseError):
    """데몬 수명주기 오류를 표현한다."""


class ValidationError(SariBaseError):
    """입력 검증 실패를 표현한다."""


class CollectionError(SariBaseError):
    """파일 수집 처리 오류를 표현한다."""


class BenchmarkError(SariBaseError):
    """벤치마크 처리 오류를 표현한다."""


class QualityError(SariBaseError):
    """L3 품질 평가 처리 오류를 표현한다."""
