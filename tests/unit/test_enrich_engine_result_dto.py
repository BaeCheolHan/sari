"""EnrichEngine 결과 DTO 분해 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.services.collection.enrich_engine import (
    EnrichEngine,
    _L2ResultBuffersDTO,
    _L3JobResultDTO,
    _L3GroupProcessor,
    _L3ResultBuffersDTO,
    _LayerUpsertBucketsDTO,
    _LayerUpsertsDTO,
)
from sari.services.collection.l2.l2_job_processor import L2JobProcessor
from sari.services.collection.l3.l3_flush_coordinator import L3FlushCoordinator
from sari.services.collection.l3.l3_result_merger import L3ResultMerger
from sari.services.collection.l3.l3_timeout_failure_builder import L3TimeoutFailureBuilder
from sari.services.collection.enrich_flush_coordinator import EnrichFlushCoordinator
from sari.services.collection.enrich_jobs_processor import EnrichJobsProcessor


def _build_result(**kwargs: object) -> _L3JobResultDTO:
    defaults: dict[str, object] = {
        "job_id": "job-1",
        "finished_status": "DONE",
        "elapsed_ms": 12.0,
        "done_id": None,
        "failure_update": None,
        "state_update": None,
        "body_delete": None,
        "lsp_update": None,
        "readiness_update": None,
        "dev_error": None,
    }
    defaults.update(kwargs)
    return _L3JobResultDTO(**defaults)


def _assert_any_import_path(source: str, candidates: tuple[str, ...]) -> None:
    assert any(candidate in source for candidate in candidates)


def test_l3_job_result_dto_accepts_layer_upserts_bundle() -> None:
    """단계별 upsert bundle을 직접 주입할 수 있어야 한다."""
    bundle = _LayerUpsertsDTO(
        l3_layer_upsert={"stage": "l3"},
        l4_layer_upsert={"stage": "l4"},
        l5_layer_upsert={"stage": "l5"},
    )
    result = _build_result(layer_upserts=bundle)

    assert result.layer_upserts == bundle
    assert result.l3_layer_upsert == {"stage": "l3"}
    assert result.l4_layer_upsert == {"stage": "l4"}
    assert result.l5_layer_upsert == {"stage": "l5"}


def test_l3_job_result_dto_keeps_legacy_layer_fields_compatible() -> None:
    """기존 l3/l4/l5 개별 필드 주입도 동일하게 동작해야 한다."""
    result = _build_result(
        l3_layer_upsert={"stage": "l3"},
        l4_layer_upsert={"stage": "l4"},
        l5_layer_upsert={"stage": "l5"},
    )

    assert result.layer_upserts.l3_layer_upsert == {"stage": "l3"}
    assert result.layer_upserts.l4_layer_upsert == {"stage": "l4"}
    assert result.layer_upserts.l5_layer_upsert == {"stage": "l5"}


def test_layer_upsert_buckets_merge_and_flush() -> None:
    """upsert bucket은 결과 병합과 repo flush를 담당해야 한다."""
    bucket = _LayerUpsertBucketsDTO.empty()
    bucket.merge_result(
        _build_result(
            l3_layer_upsert={"stage": "l3"},
            l4_layer_upsert={"stage": "l4"},
            l5_layer_upsert={"stage": "l5"},
        )
    )

    class _ToolLayerRepo:
        def __init__(self) -> None:
            self.l3_items: list[dict[str, object]] = []
            self.l4_items: list[dict[str, object]] = []
            self.l5_items: list[dict[str, object]] = []

        def upsert_l3_symbols_many(self, items: list[dict[str, object]]) -> None:
            self.l3_items.extend(items)

        def upsert_l4_normalized_symbols_many(self, items: list[dict[str, object]]) -> None:
            self.l4_items.extend(items)

        def upsert_l5_semantics_many(self, items: list[dict[str, object]]) -> None:
            self.l5_items.extend(items)

    repo = _ToolLayerRepo()
    bucket.flush(repo)

    assert repo.l3_items == [{"stage": "l3"}]
    assert repo.l4_items == [{"stage": "l4"}]
    assert repo.l5_items == [{"stage": "l5"}]
    assert bucket.l3_layer_upserts == []
    assert bucket.l4_layer_upserts == []
    assert bucket.l5_layer_upserts == []


def test_l3_result_buffers_merge_result_collects_all_channels() -> None:
    """L3 결과 버퍼는 done/failure/state/readiness 및 layer upsert를 함께 수집해야 한다."""
    buffers = _L3ResultBuffersDTO.empty()
    result = _build_result(
        done_id="done-1",
        state_update=object(),
        readiness_update=object(),
        l3_layer_upsert={"stage": "l3"},
    )
    buffers.merge_result(result)

    assert buffers.done_ids == ["done-1"]
    assert len(buffers.state_updates) == 1
    assert len(buffers.readiness_updates) == 1
    assert buffers.layer_upsert_buckets.l3_layer_upserts == [{"stage": "l3"}]


def test_enrich_engine_exposes_l3_group_processor_method() -> None:
    """L3 그룹 처리 로직은 별도 메서드로 분리되어야 한다."""
    assert hasattr(EnrichEngine, "_process_l3_group")
    assert _L3GroupProcessor is not None


def test_enrich_engine_exposes_l2_single_job_processor_method() -> None:
    """L2 단일 job 처리 로직은 전용 processor로 분리되어야 한다."""
    assert hasattr(EnrichEngine, "process_enrich_jobs_l2")
    assert hasattr(L2JobProcessor, "_process_single_l2_job")


def test_l2_result_buffers_empty_factory() -> None:
    """L2 결과 버퍼 팩토리는 비어 있는 누적 버퍼를 제공해야 한다."""
    buffers = _L2ResultBuffersDTO.empty()

    assert buffers.done_ids == []
    assert buffers.failed_updates == []
    assert buffers.state_updates == []
    assert buffers.body_deletes == []
    assert buffers.lsp_updates == []
    assert buffers.readiness_updates == []


def test_l3_group_processor_is_split_out_of_enrich_engine_module() -> None:
    """L3 group processor 구현은 별도 모듈로 분리되어야 한다."""
    source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine.py"
    ).read_text(encoding="utf-8")
    _assert_any_import_path(
        source,
        (
            "from sari.services.collection.l3.l3_group_processor import",
        ),
    )
    assert "class _L3GroupProcessor" not in source


def test_enrich_result_dto_classes_are_split_out_of_enrich_engine_module() -> None:
    """결과 DTO/버퍼 구현은 별도 모듈로 분리되어야 한다."""
    source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine.py"
    ).read_text(encoding="utf-8")
    assert "from sari.services.collection.enrich_result_dto import" in source
    assert "class _LayerUpsertsDTO" not in source
    assert "class _LayerUpsertBucketsDTO" not in source
    assert "class _L3ResultBuffersDTO" not in source
    assert "class _L2ResultBuffersDTO" not in source
    assert "class _L3JobResultDTO" not in source


def test_l2_job_processor_is_split_out_of_enrich_engine_module() -> None:
    """L2 orchestration 구현은 별도 모듈로 분리되어야 한다."""
    source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine.py"
    ).read_text(encoding="utf-8")
    # 구현 상세 import 경로보다, 런타임에서 L2 processor 위임이 유지되는지가 중요하다.
    assert "self._l2_job_processor.process_jobs(" in source
    assert "def _flush_l2_buffers(" not in source
    assert "def _process_single_l2_job(" not in source


def test_l3_flush_coordinator_is_split_out_of_enrich_engine_module() -> None:
    """L3 flush 책임은 전용 coordinator로 분리되어야 한다."""
    source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine.py"
    ).read_text(encoding="utf-8")
    assert "self._l3_flush_coordinator.flush(" in source
    assert "def _flush_l3_buffers(" not in source
    assert hasattr(L3FlushCoordinator, "flush")


def test_l3_result_merger_is_split_out_of_enrich_engine_module() -> None:
    """L3 result merge 책임은 전용 merger로 분리되어야 한다."""
    source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine.py"
    ).read_text(encoding="utf-8")
    assert "merge_l3_result=lambda result, buffers: engine._l3_result_merger.merge(" in (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine_wiring.py"
    ).read_text(encoding="utf-8")
    assert "def _merge_l3_result(" not in source
    assert hasattr(L3ResultMerger, "merge")


def test_l3_timeout_failure_builder_is_split_out_of_enrich_engine_module() -> None:
    """L3 timeout failure 합성 책임은 전용 builder로 분리되어야 한다."""
    source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine.py"
    ).read_text(encoding="utf-8")
    assert "def _build_l3_timeout_failure_result(" not in source
    assert hasattr(L3TimeoutFailureBuilder, "build")
    wiring_source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine_wiring.py"
    ).read_text(encoding="utf-8")
    assert "build_timeout_failure_result=lambda **kwargs: engine._l3_timeout_failure_builder.build(**kwargs)" in wiring_source


def test_enrich_flush_coordinator_is_split_out_of_enrich_engine_module() -> None:
    """공통 flush 책임은 전용 coordinator로 분리되어야 한다."""
    source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine.py"
    ).read_text(encoding="utf-8")
    wiring_source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine_wiring.py"
    ).read_text(encoding="utf-8")
    assert "flush_enrich=engine._enrich_flush_coordinator.flush" in wiring_source
    assert "def _flush_enrich_buffers(" not in source
    assert hasattr(EnrichFlushCoordinator, "flush")


def test_enrich_jobs_processor_is_split_out_of_enrich_engine_module() -> None:
    """L2/L3 통합 처리 오케스트레이션은 전용 processor로 분리되어야 한다."""
    source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine.py"
    ).read_text(encoding="utf-8")
    assert "self._enrich_jobs_processor.process_jobs(" in source
    assert hasattr(EnrichJobsProcessor, "process_jobs")


def test_enrich_engine_initialization_is_decomposed_into_wiring_method() -> None:
    """EnrichEngine 초기화는 전용 wiring 메서드로 분해되어야 한다."""
    source = (
        Path(__file__).resolve().parents[2] / "src" / "sari" / "services" / "collection" / "enrich_engine.py"
    ).read_text(encoding="utf-8")
    assert "self._initialize_runtime_processors()" in source
    assert "def _initialize_runtime_processors(self) -> None:" in source
