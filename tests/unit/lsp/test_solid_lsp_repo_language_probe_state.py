from __future__ import annotations

from pathlib import Path
import threading
import time

from solidlsp.ls_config import Language

from sari.db.repositories.repo_language_probe_repository import RepoLanguageProbeRepository
from sari.db.schema import init_schema
from sari.services.collection.l5.solid_lsp_extraction_backend import SolidLspExtractionBackend
from sari.services.collection.l5.solid_lsp_probe_mixin import _ProbeStateRecord


class _FakeHub:
    def get_metrics(self) -> dict[str, int]:
        return {}


def test_backend_persists_unavailable_probe_state_and_clear_removes_it(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    probe_repo = RepoLanguageProbeRepository(db_path)
    backend = SolidLspExtractionBackend(hub=_FakeHub(), repo_language_probe_repo=probe_repo)  # type: ignore[arg-type]

    backend._record_probe_state_from_extract_error(  # type: ignore[attr-defined]
        repo_root="/repo",
        relative_path="a.py",
        error_code="ERR_RUNTIME_MISMATCH",
        error_message="runtime mismatch",
    )

    rows = probe_repo.list_by_repo_root("/repo")
    assert len(rows) == 1
    assert rows[0]["language"] == "python"
    assert rows[0]["status"] == "UNAVAILABLE_COOLDOWN"
    assert rows[0]["last_error_code"] == "ERR_RUNTIME_MISMATCH"

    cleared = backend.clear_unavailable_state(repo_root="/repo", language=Language.PYTHON)
    assert cleared == 1
    assert probe_repo.list_by_repo_root("/repo") == []


def test_schedule_probe_for_file_returns_ready_without_deadlocking_on_cached_state(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    probe_repo = RepoLanguageProbeRepository(db_path)
    backend = SolidLspExtractionBackend(hub=_FakeHub(), repo_language_probe_repo=probe_repo)  # type: ignore[arg-type]
    key = (str(Path("/repo").resolve()), Language.PYTHON)
    with backend._probe_lock:
        backend._probe_state[key] = _ProbeStateRecord(status="READY_L0", last_seen_monotonic=time.monotonic())  # type: ignore[attr-defined]

    result: dict[str, str] = {}

    def _run() -> None:
        result["value"] = backend.schedule_probe_for_file(repo_root="/repo", relative_path="a.py")

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join(0.5)
    backend.shutdown_probe_executor()

    assert thread.is_alive() is False
    assert result["value"] == "ready"


def test_l1_warming_failure_preserves_short_warming_retry(monkeypatch) -> None:
    class _FakeLsp:
        pass

    class _FakeHubWithStart:
        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing") -> _FakeLsp:
            del language, repo_root, request_kind
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    def _raise_warming(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("@ERR code=ERR_LSP_INDEXING_WARMING workspace loading timeout")

    monkeypatch.setattr(
        "sari.services.collection.l5.solid_lsp_probe_mixin.request_document_symbols_with_optional_sync",
        _raise_warming,
    )
    backend = SolidLspExtractionBackend(hub=_FakeHubWithStart(), warming_retry_sec=5)  # type: ignore[arg-type]
    key = (str(Path("/repo").resolve()), Language.PYTHON)
    now = time.monotonic()
    with backend._probe_lock:
        backend._probe_state[key] = _ProbeStateRecord(
            status="READY_L0",
            last_seen_monotonic=now,
        )  # type: ignore[attr-defined]

    backend._run_l1_probe(key, "a.py")  # type: ignore[attr-defined]
    backend.shutdown_probe_executor()

    with backend._probe_lock:
        state = backend._probe_state[key]  # type: ignore[attr-defined]
    assert state.status == "WARMING"
    assert 4.5 <= state.next_retry_monotonic - now <= 5.5


def test_manual_trigger_bypasses_backpressure_cooldown(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    key = (str(Path("/repo").resolve()), Language.PYTHON)
    now = time.monotonic()
    with backend._probe_lock:
        backend._probe_state[key] = _ProbeStateRecord(
            status="BACKPRESSURE_COOLDOWN",
            fail_count=2,
            last_seen_monotonic=now,
            next_retry_monotonic=now + 60.0,
        )  # type: ignore[attr-defined]

    submitted: list[tuple[object, tuple[object, ...]]] = []

    class _FakeFuture:
        def result(self, timeout: float | None = None) -> None:
            del timeout
            return None

    def _submit(fn, *args):  # noqa: ANN001
        submitted.append((fn, args))
        return _FakeFuture()

    backend._probe_executor.submit = _submit  # type: ignore[method-assign, assignment]

    result = backend.schedule_probe_for_file(
        repo_root="/repo",
        relative_path="a.py",
        force=False,
        trigger="manual",
    )
    backend.shutdown_probe_executor()

    assert result == "scheduled"
    assert len(submitted) == 1


def test_backpressure_cooldown_blocks_background_l3_until_retry_expires() -> None:
    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    key = (str(Path("/repo").resolve()), Language.PYTHON)
    now = time.monotonic()
    with backend._probe_lock:
        backend._probe_state[key] = _ProbeStateRecord(
            status="BACKPRESSURE_COOLDOWN",
            last_seen_monotonic=now,
            next_retry_monotonic=now + 30.0,
        )  # type: ignore[attr-defined]

    try:
        assert backend.is_l3_permanently_unavailable_for_file("/repo", "a.py") is True
    finally:
        backend.shutdown_probe_executor()


def test_background_cooldown_retry_does_not_overwrite_manual_trigger() -> None:
    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    key = (str(Path("/repo").resolve()), Language.PYTHON)
    now = time.monotonic()
    with backend._probe_lock:
        backend._probe_state[key] = _ProbeStateRecord(
            status="BACKPRESSURE_COOLDOWN",
            last_seen_monotonic=now,
            next_retry_monotonic=now + 30.0,
            last_trigger="manual_probe",
        )  # type: ignore[attr-defined]

    try:
        result = backend.schedule_probe_for_file(
            repo_root="/repo",
            relative_path="a.py",
            force=False,
            trigger="background",
        )
        assert result == "cooldown"
        with backend._probe_lock:
            state = backend._probe_state[key]  # type: ignore[attr-defined]
        assert state.last_trigger == "manual_probe"
    finally:
        backend.shutdown_probe_executor()


def test_manual_retry_while_warming_preserves_manual_trigger() -> None:
    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    key = (str(Path("/repo").resolve()), Language.PYTHON)
    now = time.monotonic()
    with backend._probe_lock:
        backend._probe_state[key] = _ProbeStateRecord(
            status="WARMING",
            warming_count=1,
            last_seen_monotonic=now,
            next_retry_monotonic=now + 30.0,
            last_trigger="background",
        )  # type: ignore[attr-defined]

    try:
        result = backend.schedule_probe_for_file(
            repo_root="/repo",
            relative_path="a.py",
            force=False,
            trigger="manual",
        )
        assert result == "warming"
        with backend._probe_lock:
            state = backend._probe_state[key]  # type: ignore[attr-defined]
        assert state.last_trigger == "manual"
    finally:
        backend.shutdown_probe_executor()
