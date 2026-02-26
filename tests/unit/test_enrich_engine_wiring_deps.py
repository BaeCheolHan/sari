from __future__ import annotations

from dataclasses import is_dataclass

from sari.services.collection import enrich_engine_wiring as wiring


class _Policy:
    retry_max_attempts = 3
    retry_backoff_base_sec = 1.0


class _FileRepo:
    def get_file(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


class _ErrorPolicy:
    def record_error_event(self, **kwargs):  # noqa: ANN003
        return None


class _EventRepo:
    def record_event(self, **kwargs):  # noqa: ANN003
        return None


class _Engine:
    def __init__(self) -> None:
        self._assert_parent_alive = lambda _: None
        self._rebalance_jobs_by_language = lambda jobs: jobs
        self._file_repo = _FileRepo()
        self._policy = _Policy()
        self._persist_body_for_read = True
        self._vector_index_sink = None
        self._is_deletion_hold_enabled = lambda: False
        self._resolve_l3_skip_reason = lambda job: None
        self._build_l3_skipped_readiness = lambda job, reason, now_iso: None
        self._record_enrich_latency = lambda _: None
        self._error_policy = _ErrorPolicy()
        self._event_repo = _EventRepo()
        self._run_mode = "dev"


def test_build_enrich_processor_deps_contract() -> None:
    engine = _Engine()

    deps = wiring.build_enrich_processor_deps(engine)

    assert is_dataclass(deps)
    assert deps.retry_max_attempts == 3
    assert deps.retry_backoff_base_sec == 1.0
    assert deps.run_mode == "dev"
    assert callable(deps.file_repo_get_file)
    assert callable(deps.record_error_event)
    assert callable(deps.record_enrich_latency)
