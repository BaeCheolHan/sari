from __future__ import annotations

from pathlib import Path

from sari.db.repositories.repo_language_probe_repository import RepoLanguageProbeRepository
from sari.db.schema import init_schema


def test_repo_language_probe_repository_upserts_and_lists_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = RepoLanguageProbeRepository(db_path)

    repo.upsert_state(
        repo_root="/repo-a",
        language="python",
        status="UNAVAILABLE_COOLDOWN",
        fail_count=2,
        inflight_phase="probe",
        next_retry_at="2026-03-06T00:10:00+00:00",
        last_error_code="ERR_RPC_TIMEOUT",
        last_error_message="rpc timed out",
        last_trigger="background",
        last_seen_at="2026-03-06T00:00:00+00:00",
        updated_at="2026-03-06T00:00:01+00:00",
    )
    repo.upsert_state(
        repo_root="/repo-a",
        language="python",
        status="READY_L0",
        fail_count=0,
        inflight_phase=None,
        next_retry_at=None,
        last_error_code=None,
        last_error_message=None,
        last_trigger="interactive",
        last_seen_at="2026-03-06T00:00:02+00:00",
        updated_at="2026-03-06T00:00:03+00:00",
    )

    items = repo.list_by_repo_root("/repo-a")
    assert len(items) == 1
    first = items[0]
    assert first["repo_root"] == "/repo-a"
    assert first["language"] == "python"
    assert first["status"] == "READY_L0"
    assert first["fail_count"] == 0
    assert first["last_trigger"] == "interactive"
    assert first["updated_at"] == "2026-03-06T00:00:03+00:00"


def test_repo_language_probe_repository_clear_states_scoped(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = RepoLanguageProbeRepository(db_path)
    repo.upsert_state(
        repo_root="/repo-a",
        language="python",
        status="WORKSPACE_MISMATCH",
        fail_count=1,
        inflight_phase=None,
        next_retry_at=None,
        last_error_code="ERR_LSP_WORKSPACE_MISMATCH",
        last_error_message="mismatch",
        last_trigger="background",
        last_seen_at="2026-03-06T00:00:00+00:00",
        updated_at="2026-03-06T00:00:01+00:00",
    )
    repo.upsert_state(
        repo_root="/repo-b",
        language="java",
        status="UNAVAILABLE_COOLDOWN",
        fail_count=3,
        inflight_phase=None,
        next_retry_at="2026-03-06T00:10:00+00:00",
        last_error_code="ERR_RUNTIME_MISMATCH",
        last_error_message="runtime mismatch",
        last_trigger="background",
        last_seen_at="2026-03-06T00:00:00+00:00",
        updated_at="2026-03-06T00:00:01+00:00",
    )

    cleared = repo.clear_states(repo_root="/repo-a", language="python")

    assert cleared == 1
    assert repo.list_by_repo_root("/repo-a") == []
    assert len(repo.list_by_repo_root("/repo-b")) == 1
