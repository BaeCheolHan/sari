"""LSP 매트릭스 하드 게이트 서비스를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.exceptions import DaemonError, ErrorContext
from sari.core.language_registry import get_critical_language_names, get_enabled_language_names, normalize_language_filter
from sari.core.models import now_iso8601_utc
from sari.db.repositories.pipeline_lsp_matrix_repository import PipelineLspMatrixRepository
from sari.services.pipeline.lsp_matrix_ports import LanguageProbePort

PASS_THRESHOLD_PERCENT = 98.0


class PipelineLspMatrixService:
    """언어 readiness 매트릭스를 실행하고 게이트 판정을 수행한다."""

    def __init__(
        self,
        probe_service: LanguageProbePort,
        run_repo: PipelineLspMatrixRepository,
    ) -> None:
        """필요 의존성을 저장한다."""
        self._probe_service = probe_service
        self._run_repo = run_repo

    def run(
        self,
        repo_root: str,
        required_languages: tuple[str, ...] | None = None,
        fail_on_unavailable: bool = True,
        strict_all_languages: bool = True,
        strict_symbol_gate: bool = True,
    ) -> dict[str, object]:
        """LSP 매트릭스를 실행하고 하드 게이트를 판정한다."""
        normalized_repo = str(Path(repo_root).expanduser().resolve())
        normalized_required = self._normalize_required_languages(required_languages)
        started_at = now_iso8601_utc()
        run_id = self._run_repo.create_run(
            repo_root=normalized_repo,
            required_languages=normalized_required,
            fail_on_unavailable=fail_on_unavailable,
            strict_symbol_gate=strict_symbol_gate,
            started_at=started_at,
        )
        probe_result = self._probe_service.run(repo_root=normalized_repo)
        languages_raw = probe_result.get("languages")
        if not isinstance(languages_raw, list):
            raise DaemonError(ErrorContext(code="ERR_LSP_MATRIX_INVALID_RESULT", message="invalid language probe result"))

        enabled_languages = set(get_enabled_language_names())
        critical_languages = set(get_critical_language_names())
        checked_languages, unavailable_languages, sample_present_languages, symbol_failed_languages, missing_server_languages = self._collect_language_sets(languages_raw)
        if strict_all_languages:
            missing_languages = enabled_languages - checked_languages
            unavailable_languages.update(missing_languages)
            symbol_failed_languages.update(missing_languages)

        effective_required = self._build_required_languages(
            normalized_required=normalized_required,
            strict_all_languages=strict_all_languages,
            enabled_languages=enabled_languages,
            sample_present_languages=sample_present_languages,
        )
        failed_required = sorted(
            language
            for language in effective_required
            if (language in unavailable_languages) or (strict_symbol_gate and language in symbol_failed_languages)
        )

        coverage_total_languages, availability_scope = self._resolve_coverage_scope(
            strict_all_languages=strict_all_languages,
            enabled_languages=enabled_languages,
            effective_required=set(effective_required),
        )
        unavailable_in_scope = len([language for language in unavailable_languages if language in availability_scope])
        available_in_scope = max(0, coverage_total_languages - unavailable_in_scope)
        readiness_percent = self._calculate_percent(available=available_in_scope, total=coverage_total_languages)
        symbol_failed_in_scope = len([language for language in symbol_failed_languages if language in availability_scope])
        symbol_success_in_scope = max(0, coverage_total_languages - symbol_failed_in_scope)
        symbol_extract_success_rate = self._calculate_percent(available=symbol_success_in_scope, total=coverage_total_languages)
        strict_symbol_gate_passed = (not strict_symbol_gate) or (symbol_extract_success_rate >= PASS_THRESHOLD_PERCENT)
        critical_scope = critical_languages if strict_all_languages else critical_languages.intersection(set(effective_required))
        critical_failed_languages = sorted(
            {
                language
                for language in critical_scope
                if (language in unavailable_languages) or (strict_symbol_gate and language in symbol_failed_languages)
            }
        )
        critical_unavailable_count = len([language for language in critical_scope if language in unavailable_languages])
        critical_symbol_failed_count = len([language for language in critical_scope if language in symbol_failed_languages])
        critical_passed = (critical_unavailable_count == 0) and ((not strict_symbol_gate) or (critical_symbol_failed_count == 0))
        gate_passed = (len(failed_required) == 0) and (readiness_percent >= PASS_THRESHOLD_PERCENT) and critical_passed and strict_symbol_gate_passed
        gate_decision = "PASS" if gate_passed else "FAIL"

        summary = self._build_summary(probe_result=probe_result, languages_raw=languages_raw)
        summary["coverage_total_languages"] = coverage_total_languages
        summary["coverage_checked_languages"] = len(checked_languages)
        summary["readiness_percent"] = readiness_percent
        summary["symbol_extract_success_rate"] = symbol_extract_success_rate
        summary["missing_server_languages"] = sorted(missing_server_languages)

        result = {
            "run_id": run_id,
            "repo_root": normalized_repo,
            "started_at": started_at,
            "finished_at": now_iso8601_utc(),
            "summary": summary,
            "gate": {
                "required_languages": effective_required,
                "failed_required_languages": failed_required,
                "passed": gate_passed,
                "fail_on_unavailable": fail_on_unavailable,
                "strict_all_languages": strict_all_languages,
                "strict_symbol_gate": strict_symbol_gate,
                "strict_symbol_gate_passed": strict_symbol_gate_passed,
                "failed_symbol_languages": sorted(symbol_failed_languages),
                "pass_threshold_percent": PASS_THRESHOLD_PERCENT,
                "critical_passed": critical_passed,
                "critical_languages": sorted(critical_scope),
                "critical_failed_languages": critical_failed_languages,
                "blocking_failures": sorted(set(failed_required).union(set(critical_failed_languages))),
                "gate_decision": gate_decision,
            },
            "languages": languages_raw,
        }
        status = "COMPLETED" if gate_passed else "FAILED"
        self._run_repo.complete_run(
            run_id=run_id,
            finished_at=str(result["finished_at"]),
            status=status,
            summary=result,
        )
        if fail_on_unavailable and not gate_passed:
            failed_csv = ", ".join(failed_required)
            if len(critical_failed_languages) > 0:
                critical_csv = ", ".join(critical_failed_languages)
                raise DaemonError(
                    ErrorContext(
                        code="ERR_LSP_CRITICAL_GATE_FAILED",
                        message=f"critical languages failed: {critical_csv}; readiness_percent={readiness_percent}; symbol_extract_success_rate={symbol_extract_success_rate}; missing_server_count={len(missing_server_languages)}",
                    )
                )
            raise DaemonError(
                ErrorContext(
                    code="ERR_LSP_MATRIX_GATE_FAILED",
                    message=f"required languages unavailable: {failed_csv}; readiness_percent={readiness_percent}; symbol_extract_success_rate={symbol_extract_success_rate}; missing_server_count={len(missing_server_languages)}",
                )
            )
        return result

    def get_latest_report(self, repo_root: str) -> dict[str, object]:
        """최신 LSP 매트릭스 리포트를 반환한다."""
        normalized_repo = str(Path(repo_root).expanduser().resolve())
        latest = self._run_repo.get_latest_run()
        if latest is None:
            raise DaemonError(ErrorContext(code="ERR_LSP_MATRIX_NOT_FOUND", message="no lsp matrix run found"))
        latest_repo = latest.get("repo_root")
        if not isinstance(latest_repo, str) or latest_repo != normalized_repo:
            raise DaemonError(ErrorContext(code="ERR_LSP_MATRIX_NOT_FOUND", message="no lsp matrix run found for repo"))
        summary_payload = latest.get("summary")
        if not isinstance(summary_payload, dict):
            raise DaemonError(ErrorContext(code="ERR_LSP_MATRIX_INVALID_RESULT", message="invalid lsp matrix summary"))
        return summary_payload

    def _normalize_required_languages(self, required_languages: tuple[str, ...] | None) -> tuple[str, ...]:
        """필수 언어 입력을 정규화하고 유효성을 검증한다."""
        if required_languages is None:
            return ()
        try:
            normalized = normalize_language_filter(required_languages)
        except ValueError as exc:
            raise DaemonError(ErrorContext(code="ERR_INVALID_REQUIRED_LANGUAGE", message=str(exc))) from exc
        if normalized is None:
            return ()
        return normalized

    def _collect_language_sets(self, languages_raw: list[object]) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
        """probe 결과에서 체크/실패/샘플존재 언어 집합을 계산한다."""
        checked_languages: set[str] = set()
        unavailable_languages: set[str] = set()
        sample_present_languages: set[str] = set()
        symbol_failed_languages: set[str] = set()
        missing_server_languages: set[str] = set()
        for item in languages_raw:
            if not isinstance(item, dict):
                continue
            language = str(item.get("language", "")).strip().lower()
            if language == "":
                continue
            checked_languages.add(language)
            last_error_code = item.get("last_error_code")
            if isinstance(last_error_code, str) and last_error_code == "ERR_LANGUAGE_SAMPLE_NOT_FOUND":
                unavailable_languages.add(language)
                symbol_failed_languages.add(language)
                continue
            if isinstance(last_error_code, str) and last_error_code == "ERR_LSP_SERVER_MISSING":
                missing_server_languages.add(language)
            sample_present_languages.add(language)
            if not bool(item.get("available")):
                unavailable_languages.add(language)
            if not bool(item.get("symbol_extract_success", item.get("available"))):
                symbol_failed_languages.add(language)
        return checked_languages, unavailable_languages, sample_present_languages, symbol_failed_languages, missing_server_languages

    def _build_required_languages(
        self,
        normalized_required: tuple[str, ...],
        strict_all_languages: bool,
        enabled_languages: set[str],
        sample_present_languages: set[str],
    ) -> list[str]:
        """required 언어 집합을 strict 정책 기준으로 계산한다."""
        effective_required: set[str] = set(normalized_required)
        if strict_all_languages:
            effective_required.update(enabled_languages)
        elif len(effective_required) == 0:
            effective_required.update(sample_present_languages)
        return sorted(effective_required)

    def _resolve_coverage_scope(
        self,
        strict_all_languages: bool,
        enabled_languages: set[str],
        effective_required: set[str],
    ) -> tuple[int, set[str]]:
        """readiness 분모/분자를 계산할 언어 범위를 반환한다."""
        if strict_all_languages:
            return len(enabled_languages), enabled_languages
        return len(effective_required), effective_required

    def _calculate_percent(self, available: int, total: int) -> float:
        """가용 언어 비율(%)을 소수 2자리로 계산한다."""
        if total <= 0:
            return 0.0
        return round((float(available) / float(total)) * 100.0, 2)

    def _build_summary(self, probe_result: dict[str, object], languages_raw: list[object]) -> dict[str, object]:
        """probe 요약 페이로드를 공통 summary DTO로 변환한다."""
        summary_payload = probe_result.get("summary")
        if isinstance(summary_payload, dict):
            return {
                "total_languages": int(summary_payload.get("total_languages", len(languages_raw))),
                "available_languages": int(summary_payload.get("available_languages", 0)),
                "unavailable_languages": int(summary_payload.get("unavailable_languages", 0)),
            }
        return {
            "total_languages": len(languages_raw),
            "available_languages": len([item for item in languages_raw if isinstance(item, dict) and bool(item.get("available"))]),
            "unavailable_languages": len([item for item in languages_raw if isinstance(item, dict) and not bool(item.get("available"))]),
        }
