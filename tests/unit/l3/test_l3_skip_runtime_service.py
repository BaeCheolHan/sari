from __future__ import annotations

from dataclasses import dataclass

from solidlsp.ls_config import Language

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_skip_runtime_service import L3SkipRuntimeService


def _job() -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="src/main.py",
        content_hash="h1",
        priority=100,
        enqueue_source="scan",
        status="pending",
        attempt_count=1,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        scope_level="module",
        scope_attempts=0,
    )


@dataclass
class _State:
    tool_ready: bool
    content_hash: str
    updated_at: str


class _ReadinessRepo:
    def __init__(self, state: _State | None) -> None:
        self._state = state

    def get_state(self, repo_root: str, relative_path: str) -> _State | None:
        return self._state


class _Backend:
    def __init__(self, unavailable: bool = False) -> None:
        self._unavailable = unavailable

    def is_l3_permanently_unavailable_for_file(self, *, repo_root: str, relative_path: str) -> bool:
        return self._unavailable


def test_skip_runtime_reports_probe_unavailable() -> None:
    svc = L3SkipRuntimeService(
        l3_supported_languages={Language.PYTHON},
        l3_recent_success_ttl_sec=30,
        readiness_repo=_ReadinessRepo(None),
        lsp_backend=_Backend(unavailable=True),
        resolve_language_from_path_fn=lambda _: Language.PYTHON,
    )
    assert svc.resolve_skip_reason(_job()) == "skip_probe_unavailable"


def test_skip_runtime_recent_ready_true_when_fresh() -> None:
    svc = L3SkipRuntimeService(
        l3_supported_languages={Language.PYTHON},
        l3_recent_success_ttl_sec=60,
        readiness_repo=_ReadinessRepo(
            _State(
                tool_ready=True,
                content_hash="h1",
                updated_at="2099-01-01T00:00:00+00:00",
            )
        ),
        lsp_backend=_Backend(unavailable=False),
        resolve_language_from_path_fn=lambda _: Language.PYTHON,
    )
    assert svc.is_recent_tool_ready(_job()) is True
