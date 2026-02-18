"""LSP 매트릭스 하드 게이트 서비스를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.exceptions import DaemonError
from sari.core.language_registry import get_enabled_language_names
from sari.db.repositories.pipeline_lsp_matrix_repository import PipelineLspMatrixRepository
from sari.db.schema import init_schema
from sari.services.pipeline_lsp_matrix_service import PipelineLspMatrixService


class _FakeProbeService:
    """고정 probe 결과를 반환하는 테스트 더블이다."""

    def __init__(self, languages: list[dict[str, object]]) -> None:
        """테스트용 언어 결과를 저장한다."""
        self._languages = languages

    def run(self, repo_root: str) -> dict[str, object]:
        """고정 매트릭스 결과를 반환한다."""
        return {
            "run_id": "probe-run",
            "repo_root": repo_root,
            "started_at": "2026-02-17T20:00:00+00:00",
            "finished_at": "2026-02-17T20:00:01+00:00",
            "summary": {"total_languages": len(self._languages), "available_languages": 1, "unavailable_languages": 0},
            "languages": self._languages,
        }


def test_pipeline_lsp_matrix_service_hard_gate_raises_on_required_failure(tmp_path: Path) -> None:
    """필수 언어 실패 시 fail_on_unavailable=true면 명시 오류가 발생해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelineLspMatrixService(
        probe_service=_FakeProbeService(
            languages=[
                {"language": "python", "enabled": True, "available": True, "last_probe_at": None, "last_error_code": None, "last_error_message": None},
                {"language": "typescript", "enabled": True, "available": False, "last_probe_at": None, "last_error_code": "ERR_LSP_UNAVAILABLE", "last_error_message": "missing"},
            ]
        ),
        run_repo=PipelineLspMatrixRepository(db_path),
    )
    try:
        service.run(
            repo_root="/repo",
            required_languages=("python", "typescript"),
            fail_on_unavailable=True,
            strict_all_languages=False,
        )
        assert False, "expected DaemonError"
    except DaemonError as exc:
        assert exc.context.code in {"ERR_LSP_MATRIX_GATE_FAILED", "ERR_LSP_CRITICAL_GATE_FAILED"}


def test_pipeline_lsp_matrix_service_report_returns_latest(tmp_path: Path) -> None:
    """latest report 조회가 가능해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelineLspMatrixService(
        probe_service=_FakeProbeService(
            languages=[
                {"language": "python", "enabled": True, "available": True, "last_probe_at": None, "last_error_code": None, "last_error_message": None},
            ]
        ),
        run_repo=PipelineLspMatrixRepository(db_path),
    )
    result = service.run(
        repo_root="/repo",
        required_languages=("python",),
        fail_on_unavailable=True,
        strict_all_languages=False,
    )
    assert result["gate"]["passed"] is True

    latest = service.get_latest_report(repo_root="/repo")
    assert latest["gate"]["passed"] is True


def test_pipeline_lsp_matrix_service_enforces_98_percent_gate_and_critical_pass(tmp_path: Path) -> None:
    """전언어 강제 모드에서는 98% 기준과 critical 언어 성공 여부를 함께 판정해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    enabled_languages = get_enabled_language_names()
    languages: list[dict[str, object]] = []
    for language in enabled_languages:
        available = language not in {"python", "typescript"}
        languages.append(
            {
                "language": language,
                "enabled": True,
                "available": available,
                "last_probe_at": None,
                "last_error_code": ("ERR_LSP_UNAVAILABLE" if not available else None),
                "last_error_message": ("missing" if not available else None),
            }
        )
    service = PipelineLspMatrixService(
        probe_service=_FakeProbeService(languages=languages),
        run_repo=PipelineLspMatrixRepository(db_path),
    )
    result = service.run(
        repo_root="/repo",
        required_languages=None,
        fail_on_unavailable=False,
        strict_all_languages=True,
    )
    gate = result["gate"]
    summary = result["summary"]
    assert gate["pass_threshold_percent"] == 98.0
    assert gate["critical_passed"] is False
    assert "python" in gate["critical_languages"]
    assert "python" in gate["critical_failed_languages"]
    assert "python" in gate["blocking_failures"]
    assert gate["gate_decision"] == "FAIL"
    assert gate["passed"] is False
    assert summary["coverage_total_languages"] == len(enabled_languages)
    assert summary["coverage_checked_languages"] == len(enabled_languages)
    assert isinstance(summary["readiness_percent"], float)


def test_pipeline_lsp_matrix_service_enforces_strict_symbol_gate(tmp_path: Path) -> None:
    """strict_symbol_gate=true에서는 심볼 추출 성공률/필수언어 실패를 함께 차단해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelineLspMatrixService(
        probe_service=_FakeProbeService(
            languages=[
                {
                    "language": "python",
                    "enabled": True,
                    "available": True,
                    "symbol_extract_success": True,
                    "last_probe_at": None,
                    "last_error_code": None,
                    "last_error_message": None,
                },
                {
                    "language": "typescript",
                    "enabled": True,
                    "available": True,
                    "symbol_extract_success": False,
                    "last_probe_at": None,
                    "last_error_code": "ERR_LSP_DOCUMENT_SYMBOL_FAILED",
                    "last_error_message": "symbol failed",
                },
            ]
        ),
        run_repo=PipelineLspMatrixRepository(db_path),
    )

    result = service.run(
        repo_root="/repo",
        required_languages=("python", "typescript"),
        fail_on_unavailable=False,
        strict_all_languages=False,
        strict_symbol_gate=True,
    )
    assert result["gate"]["strict_symbol_gate"] is True
    assert result["gate"]["strict_symbol_gate_passed"] is False
    assert result["gate"]["failed_required_languages"] == ["typescript"]
    assert result["summary"]["symbol_extract_success_rate"] == 50.0


def test_pipeline_lsp_matrix_service_collects_missing_server_languages(tmp_path: Path) -> None:
    """미설치 언어 서버는 summary.missing_server_languages로 집계되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelineLspMatrixService(
        probe_service=_FakeProbeService(
            languages=[
                {
                    "language": "python",
                    "enabled": True,
                    "available": False,
                    "symbol_extract_success": False,
                    "last_probe_at": None,
                    "last_error_code": "ERR_LSP_SERVER_MISSING",
                    "last_error_message": "command not found",
                },
            ]
        ),
        run_repo=PipelineLspMatrixRepository(db_path),
    )
    result = service.run(
        repo_root="/repo",
        required_languages=("python",),
        fail_on_unavailable=False,
        strict_all_languages=False,
        strict_symbol_gate=True,
    )
    assert result["summary"]["missing_server_languages"] == ["python"]
    assert result["gate"]["failed_required_languages"] == ["python"]


def test_pipeline_lsp_matrix_service_raises_critical_gate_error(tmp_path: Path) -> None:
    """critical 언어 실패 시 fail_on_unavailable=true에서 전용 오류코드를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelineLspMatrixService(
        probe_service=_FakeProbeService(
            languages=[
                {
                    "language": "python",
                    "enabled": True,
                    "available": False,
                    "symbol_extract_success": False,
                    "last_probe_at": None,
                    "last_error_code": "ERR_LSP_UNAVAILABLE",
                    "last_error_message": "down",
                },
                {
                    "language": "typescript",
                    "enabled": True,
                    "available": True,
                    "symbol_extract_success": True,
                    "last_probe_at": None,
                    "last_error_code": None,
                    "last_error_message": None,
                },
            ]
        ),
        run_repo=PipelineLspMatrixRepository(db_path),
    )
    try:
        service.run(
            repo_root="/repo",
            required_languages=("python", "typescript"),
            fail_on_unavailable=True,
            strict_all_languages=False,
            strict_symbol_gate=True,
        )
        assert False, "expected DaemonError"
    except DaemonError as exc:
        assert exc.context.code == "ERR_LSP_CRITICAL_GATE_FAILED"


def test_pipeline_lsp_matrix_service_strict_all_false_uses_effective_scope(tmp_path: Path) -> None:
    """strict_all_languages=false에서는 readiness/critical 분모를 effective_required 기준으로 계산해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    enabled_languages = get_enabled_language_names()
    languages: list[dict[str, object]] = []
    for language in enabled_languages:
        if language == "python":
            languages.append(
                {
                    "language": language,
                    "enabled": True,
                    "available": True,
                    "symbol_extract_success": True,
                    "last_probe_at": None,
                    "last_error_code": None,
                    "last_error_message": None,
                }
            )
            continue
        languages.append(
            {
                "language": language,
                "enabled": True,
                "available": False,
                "symbol_extract_success": False,
                "last_probe_at": None,
                "last_error_code": "ERR_LANGUAGE_SAMPLE_NOT_FOUND",
                "last_error_message": "sample missing",
            }
        )
    service = PipelineLspMatrixService(
        probe_service=_FakeProbeService(languages=languages),
        run_repo=PipelineLspMatrixRepository(db_path),
    )
    result = service.run(
        repo_root="/repo",
        required_languages=("python",),
        fail_on_unavailable=False,
        strict_all_languages=False,
        strict_symbol_gate=True,
    )
    assert result["gate"]["passed"] is True
    assert result["summary"]["coverage_total_languages"] == 1
    assert result["summary"]["readiness_percent"] == 100.0
    assert result["summary"]["symbol_extract_success_rate"] == 100.0
    assert result["gate"]["critical_passed"] is True
