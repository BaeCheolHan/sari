"""파이프라인 L3 품질 평가 서비스를 구현한다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sari.core.config import DEFAULT_COLLECTION_EXCLUDE_GLOBS
from sari.core.exceptions import DaemonError, ErrorContext, QualityError
from sari.core.language_registry import get_default_collection_extensions, normalize_language_filter, resolve_language_from_path
from sari.core.models import CollectionPolicyDTO, L3DiffResultDTO, L3ReferenceDataDTO, now_iso8601_utc
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_quality_repository import PipelineQualityRepository
from sari.lsp.hub import LspHub
from sari.lsp.path_normalizer import normalize_repo_relative_path
from sari.services.file_collection_service import LspExtractionBackend, LspExtractionResultDTO
from solidlsp.ls_exceptions import SolidLSPException


@dataclass(frozen=True)
class _SetCountsDTO:
    """집합 정밀도 계산에 필요한 카운트를 표현한다."""

    tp: int
    fp: int
    fn: int


class MirrorGoldenBackend(LspExtractionBackend):
    """현재 L3 저장 결과를 골든으로 반영하는 테스트/로컬 백엔드다."""

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        """호출 경로 일관성을 위해 빈 참조를 반환한다."""
        del repo_root, relative_path, content_hash
        return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)


class SerenaGoldenBackend(LspExtractionBackend):
    """Serena solidlsp 경로를 사용해 골든 L3 참조를 추출한다."""

    def __init__(self, hub: LspHub) -> None:
        """LSP hub 의존성을 저장한다."""
        self._hub = hub
        self._request_count = 0
        self._fallback_count = 0
        self._fallback_reason_counts: dict[str, int] = {}

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        """파일 기준 골든 심볼/호출자 데이터를 추출한다."""
        del content_hash
        self._request_count += 1
        try:
            language = self._hub.resolve_language(relative_path)
            lsp = self._hub.get_or_start(language=language, repo_root=repo_root)
            try:
                raw_symbols = list(lsp.request_document_symbols(relative_path).iter_symbols())
                return LspExtractionResultDTO(
                    symbols=self._convert_symbols(raw_symbols=raw_symbols, relative_path=relative_path),
                    relations=[],
                    error_message=None,
                )
            except (SolidLSPException, RuntimeError, OSError, ValueError, TypeError) as doc_exc:
                query = Path(relative_path).stem
                workspace_symbols = lsp.request_workspace_symbol(query) or []
                self._fallback_count += 1
                reason = type(doc_exc).__name__
                self._fallback_reason_counts[reason] = self._fallback_reason_counts.get(reason, 0) + 1
                return LspExtractionResultDTO(
                    symbols=self._convert_symbols(raw_symbols=workspace_symbols, relative_path=relative_path),
                    relations=[],
                    error_message=f"fallback:documentSymbol->{type(doc_exc).__name__}",
                )
        except (DaemonError, SolidLSPException, RuntimeError, OSError, ValueError, TypeError) as exc:
            return LspExtractionResultDTO(symbols=[], relations=[], error_message=f"serena golden 추출 실패: {exc}")

    def _convert_symbols(self, raw_symbols: list[object], relative_path: str) -> list[dict[str, object]]:
        """LSP raw 심볼을 품질 비교용 심볼로 변환한다."""
        normalized_relative_path = normalize_repo_relative_path(relative_path)
        symbols: list[dict[str, object]] = []
        for raw in raw_symbols:
            if not isinstance(raw, dict):
                continue
            location = raw.get("location")
            if isinstance(location, dict):
                rel = location.get("relativePath")
                if isinstance(rel, str) and normalize_repo_relative_path(rel) != normalized_relative_path:
                    continue
            line = 0
            end_line = 0
            range_data = None
            if isinstance(location, dict):
                range_data = location.get("range")
            if isinstance(range_data, dict):
                start_data = range_data.get("start")
                end_data = range_data.get("end")
                if isinstance(start_data, dict):
                    line = int(start_data.get("line", 0))
                if isinstance(end_data, dict):
                    end_line = int(end_data.get("line", line))
            symbols.append(
                {
                    "name": str(raw.get("name", "")),
                    "kind": str(raw.get("kind", "")),
                    "line": line,
                    "end_line": end_line,
                }
            )
        return symbols

    def reset_stats(self) -> None:
        """골든 추출 통계를 초기화한다."""
        self._request_count = 0
        self._fallback_count = 0
        self._fallback_reason_counts = {}

    def stats(self) -> dict[str, int]:
        """골든 추출 통계를 반환한다."""
        payload: dict[str, int] = {"request_count": self._request_count, "fallback_count": self._fallback_count}
        for reason, count in sorted(self._fallback_reason_counts.items(), key=lambda item: item[0]):
            payload[f"fallback_reason_{reason}"] = count
        return payload


class PipelineQualityService:
    """L3 정확도와 오류율을 평가하고 리포트를 생성한다."""

    def __init__(
        self,
        file_repo: FileCollectionRepository,
        lsp_repo: LspToolDataRepository,
        quality_repo: PipelineQualityRepository,
        golden_backend: LspExtractionBackend,
        artifact_root: Path,
    ) -> None:
        """품질 평가 의존성을 주입한다."""
        self._file_repo = file_repo
        self._lsp_repo = lsp_repo
        self._quality_repo = quality_repo
        self._golden_backend = golden_backend
        self._artifact_root = artifact_root

    @staticmethod
    def default_collection_policy() -> CollectionPolicyDTO:
        """품질 측정용 기본 수집 정책을 반환한다."""
        return CollectionPolicyDTO(
            include_ext=get_default_collection_extensions(),
            exclude_globs=DEFAULT_COLLECTION_EXCLUDE_GLOBS,
            max_file_size_bytes=512 * 1024,
            scan_interval_sec=180,
            max_enrich_batch=200,
            retry_max_attempts=2,
            retry_backoff_base_sec=1,
            queue_poll_interval_ms=100,
        )

    def run(
        self,
        repo_root: str,
        limit_files: int,
        profile: str,
        language_filter: tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        """L3 품질 평가를 실행하고 요약 결과를 반환한다."""
        root = str(Path(repo_root).expanduser().resolve())
        if limit_files <= 0:
            raise QualityError(ErrorContext(code="ERR_INVALID_LIMIT_FILES", message="limit_files는 1 이상이어야 합니다"))
        try:
            normalized_filter = normalize_language_filter(language_filter)
        except ValueError as exc:
            raise QualityError(ErrorContext(code="ERR_INVALID_LANGUAGE_FILTER", message=str(exc))) from exc
        started_at = now_iso8601_utc()
        run_id = self._quality_repo.create_run(repo_root=root, limit_files=limit_files, profile=profile, started_at=started_at)
        try:
            files = self._file_repo.list_files(repo_root=root, limit=limit_files)
            if len(files) == 0:
                raise QualityError(ErrorContext(code="ERR_QUALITY_EMPTY_DATASET", message="index된 파일이 없습니다"))
            if isinstance(self._golden_backend, SerenaGoldenBackend):
                self._golden_backend.reset_stats()

            symbol_counts = _SetCountsDTO(tp=0, fp=0, fn=0)
            caller_counts = _SetCountsDTO(tp=0, fp=0, fn=0)
            error_count = 0
            diff_items: list[dict[str, object]] = []
            per_language_totals: dict[str, dict[str, int]] = {}
            evaluated_files = 0

            for file_item in files:
                language = resolve_language_from_path(file_item.relative_path)
                language_name = "unknown" if language is None else language.value
                if normalized_filter is not None and language_name not in normalized_filter:
                    continue
                evaluated_files += 1
                if language_name not in per_language_totals:
                    per_language_totals[language_name] = {
                        "symbol_tp": 0,
                        "symbol_fp": 0,
                        "symbol_fn": 0,
                        "caller_tp": 0,
                        "caller_fp": 0,
                        "caller_fn": 0,
                        "evaluated_files": 0,
                        "error_files": 0,
                    }
                per_language_totals[language_name]["evaluated_files"] += 1
                predicted = L3ReferenceDataDTO(
                    symbols=self._lsp_repo.list_file_symbols(root, file_item.relative_path, file_item.content_hash),
                    relations=self._lsp_repo.list_file_relations(root, file_item.relative_path, file_item.content_hash),
                    error_message=None,
                )
                golden_raw = self._golden_backend.extract(root, file_item.relative_path, file_item.content_hash)
                golden = L3ReferenceDataDTO(
                    symbols=golden_raw.symbols,
                    relations=golden_raw.relations,
                    error_message=golden_raw.error_message,
                )
                diff = self._diff_file(predicted=predicted, golden=golden)
                if golden.has_error():
                    error_count += 1
                    per_language_totals[language_name]["error_files"] += 1
                symbol_counts = _SetCountsDTO(
                    tp=symbol_counts.tp + diff.symbol_tp,
                    fp=symbol_counts.fp + diff.symbol_fp,
                    fn=symbol_counts.fn + diff.symbol_fn,
                )
                caller_counts = _SetCountsDTO(
                    tp=caller_counts.tp + diff.caller_tp,
                    fp=caller_counts.fp + diff.caller_fp,
                    fn=caller_counts.fn + diff.caller_fn,
                )
                per_language_totals[language_name]["symbol_tp"] += diff.symbol_tp
                per_language_totals[language_name]["symbol_fp"] += diff.symbol_fp
                per_language_totals[language_name]["symbol_fn"] += diff.symbol_fn
                per_language_totals[language_name]["caller_tp"] += diff.caller_tp
                per_language_totals[language_name]["caller_fp"] += diff.caller_fp
                per_language_totals[language_name]["caller_fn"] += diff.caller_fn
                diff_items.append(
                    {
                        "language": language_name,
                        "relative_path": file_item.relative_path,
                        "content_hash": file_item.content_hash,
                        "diff": diff.to_dict(),
                    }
                )
            if evaluated_files == 0:
                raise QualityError(ErrorContext(code="ERR_QUALITY_EMPTY_DATASET", message="language filter에 해당하는 파일이 없습니다"))

            symbol_precision = _precision_percent(symbol_counts.tp, symbol_counts.fp)
            caller_precision = _precision_percent(caller_counts.tp, caller_counts.fp)
            total_precision = _weighted_average(
                symbol_precision=symbol_precision,
                caller_precision=caller_precision,
                symbol_weight=symbol_counts.tp + symbol_counts.fp,
                caller_weight=caller_counts.tp + caller_counts.fp,
            )
            error_rate = _ratio_percent(error_count, evaluated_files)
            per_language: list[dict[str, object]] = []
            per_language_all_passed = True
            for language_name, totals in sorted(per_language_totals.items(), key=lambda item: item[0]):
                per_symbol_precision = _precision_percent(totals["symbol_tp"], totals["symbol_fp"])
                per_caller_precision = _precision_percent(totals["caller_tp"], totals["caller_fp"])
                per_total_precision = _weighted_average(
                    symbol_precision=per_symbol_precision,
                    caller_precision=per_caller_precision,
                    symbol_weight=totals["symbol_tp"] + totals["symbol_fp"],
                    caller_weight=totals["caller_tp"] + totals["caller_fp"],
                )
                per_error_rate = _ratio_percent(totals["error_files"], totals["evaluated_files"])
                per_passed = per_total_precision >= 95.0 and per_error_rate <= 1.0
                if not per_passed:
                    per_language_all_passed = False
                per_language.append(
                    {
                        "language": language_name,
                        "evaluated_files": totals["evaluated_files"],
                        "error_files": totals["error_files"],
                        "error_rate": per_error_rate,
                        "precision_symbol": per_symbol_precision,
                        "precision_caller": per_caller_precision,
                        "precision_total": per_total_precision,
                        "gate_passed": per_passed,
                    }
                )
            gate_passed = total_precision >= 95.0 and error_rate <= 1.0 and per_language_all_passed

            summary = {
                "run_id": run_id,
                "status": "PASSED" if gate_passed else "FAILED",
                "repo_root": root,
                "limit_files": limit_files,
                "profile": profile,
                "language_filter": [] if normalized_filter is None else list(normalized_filter),
                "evaluated_files": evaluated_files,
                "error_files": error_count,
                "error_rate": error_rate,
                "precision": {
                    "symbol": symbol_precision,
                    "caller": caller_precision,
                    "total": total_precision,
                },
                "per_language": per_language,
                "thresholds": {
                    "precision_min": 95.0,
                    "error_rate_max": 1.0,
                },
                "totals": {
                    "symbol_tp": symbol_counts.tp,
                    "symbol_fp": symbol_counts.fp,
                    "symbol_fn": symbol_counts.fn,
                    "caller_tp": caller_counts.tp,
                    "caller_fp": caller_counts.fp,
                    "caller_fn": caller_counts.fn,
                },
                "samples": diff_items[:100],
            }
            if isinstance(self._golden_backend, SerenaGoldenBackend):
                summary["golden_backend"] = self._golden_backend.stats()
            self._write_artifact(run_id=run_id, summary=summary)
            self._quality_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status=str(summary["status"]),
                summary=summary,
            )
            return summary
        except QualityError as exc:
            failed = {
                "run_id": run_id,
                "status": "FAILED",
                "repo_root": root,
                "limit_files": limit_files,
                "profile": profile,
                "error": str(exc),
            }
            self._quality_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status="FAILED",
                summary=failed,
            )
            raise

    def get_latest_report(self, repo_root: str) -> dict[str, object]:
        """최신 품질 리포트를 반환한다."""
        latest = self._quality_repo.get_latest_run()
        if latest is None:
            raise QualityError(ErrorContext(code="ERR_QUALITY_NOT_FOUND", message="no quality run found"))
        summary = latest.get("summary")
        if not isinstance(summary, dict):
            raise QualityError(ErrorContext(code="ERR_QUALITY_NOT_FOUND", message="no quality run found"))
        summary_repo = str(summary.get("repo_root", ""))
        normalized_repo = str(Path(repo_root).expanduser().resolve())
        if summary_repo != normalized_repo:
            raise QualityError(ErrorContext(code="ERR_QUALITY_NOT_FOUND", message="no quality run found for repo"))
        return summary

    def _diff_file(self, predicted: L3ReferenceDataDTO, golden: L3ReferenceDataDTO) -> L3DiffResultDTO:
        """파일 단위 predicted/golden 차이를 계산한다."""
        predicted_symbols = {_symbol_key(item) for item in predicted.symbols}
        golden_symbols = {_symbol_key(item) for item in golden.symbols}
        predicted_callers = {_caller_key(item) for item in predicted.relations}
        golden_callers = {_caller_key(item) for item in golden.relations}

        symbol_tp = len(predicted_symbols.intersection(golden_symbols))
        symbol_fp = len(predicted_symbols.difference(golden_symbols))
        symbol_fn = len(golden_symbols.difference(predicted_symbols))
        caller_tp = len(predicted_callers.intersection(golden_callers))
        caller_fp = len(predicted_callers.difference(golden_callers))
        caller_fn = len(golden_callers.difference(predicted_callers))

        return L3DiffResultDTO(
            symbol_tp=symbol_tp,
            symbol_fp=symbol_fp,
            symbol_fn=symbol_fn,
            caller_tp=caller_tp,
            caller_fp=caller_fp,
            caller_fn=caller_fn,
            error_message=golden.error_message,
        )

    def _write_artifact(self, run_id: str, summary: dict[str, object]) -> None:
        """품질 평가 아티팩트를 저장한다."""
        quality_dir = self._artifact_root / "quality"
        quality_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = quality_dir / f"{run_id}.json"
        artifact_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _symbol_key(item: dict[str, object]) -> str:
    """심볼 비교용 키 문자열을 생성한다."""
    return "|".join(
        [
            str(item.get("name", "")),
            str(item.get("kind", "")),
            str(int(item.get("line", 0))),
            str(int(item.get("end_line", 0))),
        ]
    )


def _caller_key(item: dict[str, object]) -> str:
    """호출자 비교용 키 문자열을 생성한다."""
    return "|".join(
        [
            str(item.get("from_symbol", "")),
            str(item.get("to_symbol", "")),
            str(int(item.get("line", 0))),
        ]
    )


def _precision_percent(tp: int, fp: int) -> float:
    """정밀도 백분율을 계산한다."""
    denominator = tp + fp
    if denominator == 0:
        return 100.0
    return (float(tp) * 100.0) / float(denominator)


def _ratio_percent(numerator: int, denominator: int) -> float:
    """비율 백분율을 계산한다."""
    if denominator == 0:
        return 0.0
    return (float(numerator) * 100.0) / float(denominator)


def _weighted_average(symbol_precision: float, caller_precision: float, symbol_weight: int, caller_weight: int) -> float:
    """심볼/호출자 정밀도의 가중 평균을 계산한다."""
    total_weight = symbol_weight + caller_weight
    if total_weight == 0:
        return 100.0
    return ((symbol_precision * float(symbol_weight)) + (caller_precision * float(caller_weight))) / float(total_weight)
