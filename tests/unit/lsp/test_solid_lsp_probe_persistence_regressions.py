from __future__ import annotations

import threading
import time
from pathlib import Path

from solidlsp.ls_config import Language

from sari.db.repositories.repo_language_probe_repository import RepoLanguageProbeRepository
from sari.db.schema import init_schema
from sari.services.collection.l5.solid_lsp_extraction_backend import SolidLspExtractionBackend
from sari.services.collection.l5.solid_lsp_probe_mixin import _ProbeStateRecord


class _FakeHub:
    def get_metrics(self) -> dict[str, int]:
        return {}

    def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
        del language, repo_root

    def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
        del language, repo_root, request_kind

        class _FakeLsp:
            def request_document_symbols(self, relative_path: str):  # noqa: ANN001
                del relative_path
                raise RuntimeError("workspace loading timeout")

        return _FakeLsp()


def test_schedule_probe_ready_cached_does_not_deadlock_with_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    backend = SolidLspExtractionBackend(
        hub=_FakeHub(),  # type: ignore[arg-type]
        repo_language_probe_repo=RepoLanguageProbeRepository(db_path),
    )
    key = (str(Path("/repo").resolve()), Language.PYTHON)
    with backend._probe_lock:
        backend._probe_state[key] = _ProbeStateRecord(status="READY_L0", last_seen_monotonic=time.monotonic())  # type: ignore[attr-defined]

    result: dict[str, str] = {}

    def _invoke() -> None:
        result["value"] = backend.schedule_probe_for_file("/repo", "a.py")

    thread = threading.Thread(target=_invoke)
    thread.start()
    thread.join(timeout=1.0)
    backend.shutdown_probe_executor()

    assert thread.is_alive() is False
    assert result["value"] == "ready"


def test_run_l1_probe_preserves_short_warming_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    backend = SolidLspExtractionBackend(
        hub=_FakeHub(),  # type: ignore[arg-type]
        repo_language_probe_repo=RepoLanguageProbeRepository(db_path),
        probe_workers=1,
        l1_workers=1,
        warming_retry_sec=5,
    )
    key = (str(Path("/repo").resolve()), Language.PYTHON)
    start = time.monotonic()
    with backend._probe_lock:
        backend._probe_state[key] = _ProbeStateRecord(status="READY_L0", last_seen_monotonic=start)  # type: ignore[attr-defined]

    backend._run_l1_probe(key, "a.py")  # type: ignore[attr-defined]

    state = backend._probe_state[key]  # type: ignore[attr-defined]
    delta = state.next_retry_monotonic - start
    backend.shutdown_probe_executor()

    assert state.status == "WARMING"
    assert 0.0 < delta < 10.0
