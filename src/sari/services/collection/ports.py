"""파일 수집 런타임 포트 인터페이스를 정의한다."""

from __future__ import annotations

from typing import Protocol

from sari.core.models import CollectionScanResultDTO, FileReadResultDTO, PipelineMetricsDTO


class CollectionScanPort(Protocol):
    """L1 스캔/파일 조회 계층 포트를 정의한다."""

    def scan_once(self, repo_root: str) -> CollectionScanResultDTO:
        """저장소 전체를 1회 스캔한다."""
        ...

    def index_file(self, repo_root: str, relative_path: str) -> CollectionScanResultDTO:
        """단일 파일을 증분 인덱싱한다."""
        ...

    def list_files(self, repo_root: str, limit: int, prefix: str | None) -> list[dict[str, object]]:
        """저장소 내 인덱싱 파일 목록을 조회한다."""
        ...

    def read_file(self, repo_root: str, relative_path: str, offset: int, limit: int | None) -> FileReadResultDTO:
        """인덱싱 파일 본문을 읽는다."""
        ...


class CollectionPipelinePort(Protocol):
    """L2/L3 파이프라인 실행 포트를 정의한다."""

    def process_enrich_jobs(self, limit: int) -> int:
        """L2/L3 통합 보강 작업을 수행한다."""
        ...

    def process_enrich_jobs_l2(self, limit: int) -> int:
        """L2 보강 작업을 수행한다."""
        ...

    def process_enrich_jobs_l3(self, limit: int) -> int:
        """L3 보강 작업을 수행한다."""
        ...


class CollectionLifecyclePort(Protocol):
    """백그라운드 수집 런타임 생명주기 포트를 정의한다."""

    def start_background(self) -> None:
        """백그라운드 루프를 시작한다."""
        ...

    def stop_background(self) -> None:
        """백그라운드 루프를 중지한다."""
        ...


class CollectionObservabilityPort(Protocol):
    """파이프라인 관측/오류 조회 포트를 정의한다."""

    def get_pipeline_metrics(self) -> PipelineMetricsDTO:
        """파이프라인 메트릭을 조회한다."""
        ...

    def list_error_events(
        self,
        limit: int,
        offset: int = 0,
        repo_root: str | None = None,
        error_code: str | None = None,
    ) -> list[dict[str, object]]:
        """오류 이벤트 목록을 조회한다."""
        ...

    def get_error_event(self, event_id: str) -> dict[str, object] | None:
        """오류 이벤트 단건을 조회한다."""
        ...


class CollectionRuntimePort(
    CollectionScanPort,
    CollectionPipelinePort,
    CollectionLifecyclePort,
    CollectionObservabilityPort,
    Protocol,
):
    """수집 런타임 전체 기능을 제공하는 통합 포트다."""

