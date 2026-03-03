"""L3 AST vs LSP shadow 품질 계측 트래커."""

from __future__ import annotations

import hashlib

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_treesitter_preprocess_service import L3PreprocessResultDTO


class L3QualityShadowTracker:
    """orchestrator 인스턴스 상태를 기반으로 shadow 지표를 관리한다."""

    def __init__(self, owner: object) -> None:
        self._owner = owner

    def get_summary(self) -> dict[str, object]:
        if not bool(getattr(self._owner, "_quality_shadow_enabled", False)):
            return {
                "enabled": False,
                "sampled_files": int(getattr(self._owner, "_quality_shadow_sampled_count", 0)),
                "shadow_eval_errors": int(getattr(self._owner, "_quality_shadow_eval_errors", 0)),
            }
        sampled_files_by_language: dict[str, int] = {}
        avg_recall_proxy_by_language: dict[str, float] = {}
        avg_precision_proxy_by_language: dict[str, float] = {}
        avg_kind_match_rate_by_language: dict[str, float] = {}
        avg_position_match_rate_by_language: dict[str, float] = {}
        avg_position_match_rate_relaxed_by_language: dict[str, float] = {}
        missing_patterns_top_by_language: dict[str, list[dict[str, object]]] = {}
        for language, acc in getattr(self._owner, "_quality_shadow_accumulators", {}).items():
            count = int(float(acc.get("count", 0.0)))
            if count <= 0:
                continue
            denom = float(count)
            sampled_files_by_language[str(language)] = count
            avg_recall_proxy_by_language[str(language)] = float(acc.get("recall_sum", 0.0)) / denom
            avg_precision_proxy_by_language[str(language)] = float(acc.get("precision_sum", 0.0)) / denom
            avg_kind_match_rate_by_language[str(language)] = float(acc.get("kind_sum", 0.0)) / denom
            avg_position_match_rate_by_language[str(language)] = float(acc.get("position_sum", 0.0)) / denom
            avg_position_match_rate_relaxed_by_language[str(language)] = float(
                acc.get("position_relaxed_sum", 0.0)
            ) / denom
            per_language_missing = dict(getattr(self._owner, "_quality_shadow_missing_pattern_counts", {}).get(language, {}))
            top_items = sorted(per_language_missing.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]
            missing_patterns_top_by_language[str(language)] = [
                {"pattern": str(pattern), "count": int(value)} for pattern, value in top_items
            ]
        return {
            "enabled": True,
            "sampled_files": int(getattr(self._owner, "_quality_shadow_sampled_count", 0)),
            "sampled_files_by_language": sampled_files_by_language,
            "avg_recall_proxy_by_language": avg_recall_proxy_by_language,
            "avg_precision_proxy_by_language": avg_precision_proxy_by_language,
            "avg_kind_match_rate_by_language": avg_kind_match_rate_by_language,
            "avg_position_match_rate_by_language": avg_position_match_rate_by_language,
            "avg_position_match_rate_relaxed_by_language": avg_position_match_rate_relaxed_by_language,
            "quality_flags_top_counts": dict(getattr(self._owner, "_quality_shadow_flag_counts", {})),
            "missing_patterns_top_by_language": missing_patterns_top_by_language,
            "shadow_eval_errors": int(getattr(self._owner, "_quality_shadow_eval_errors", 0)),
        }

    def record_compare(
        self,
        *,
        job: FileEnrichJobDTO,
        language: str,
        preprocess_result: L3PreprocessResultDTO | None,
        lsp_symbols: list[dict[str, object]],
    ) -> None:
        if not bool(getattr(self._owner, "_quality_shadow_enabled", False)):
            return
        eval_service = getattr(self._owner, "_quality_eval_service", None)
        if eval_service is None:
            return
        normalized_language = str(language).strip().lower()
        allowlist = getattr(self._owner, "_quality_shadow_lang_allowlist", set())
        if len(allowlist) > 0 and normalized_language not in allowlist:
            return
        if preprocess_result is None or len(preprocess_result.symbols) == 0:
            return
        max_files = int(getattr(self._owner, "_quality_shadow_max_files", 0))
        if max_files > 0 and int(getattr(self._owner, "_quality_shadow_sampled_count", 0)) >= max_files:
            return
        if not self._should_sample(job=job, language=normalized_language):
            return
        try:
            result = eval_service.evaluate(
                language=normalized_language,
                ast_symbols=list(preprocess_result.symbols),
                lsp_symbols=list(lsp_symbols),
            )
        except (RuntimeError, OSError, ValueError, TypeError):
            self._owner._quality_shadow_eval_errors = int(getattr(self._owner, "_quality_shadow_eval_errors", 0)) + 1
            return
        self._owner._quality_shadow_sampled_count = int(getattr(self._owner, "_quality_shadow_sampled_count", 0)) + 1
        accumulators = getattr(self._owner, "_quality_shadow_accumulators")
        acc = accumulators.setdefault(
            normalized_language,
            {"count": 0.0, "recall_sum": 0.0, "precision_sum": 0.0, "kind_sum": 0.0, "position_sum": 0.0},
        )
        acc["count"] += 1.0
        acc["recall_sum"] += float(result.symbol_recall_proxy)
        acc["precision_sum"] += float(result.symbol_precision_proxy)
        acc["kind_sum"] += float(result.kind_match_rate)
        acc["position_sum"] += float(result.position_match_rate)
        acc["position_relaxed_sum"] = float(acc.get("position_relaxed_sum", 0.0)) + float(
            getattr(result, "position_match_rate_relaxed", result.position_match_rate)
        )
        flag_counts = getattr(self._owner, "_quality_shadow_flag_counts")
        for flag in getattr(result, "quality_flags", ()):
            key = str(flag).strip()
            if key == "":
                continue
            flag_counts[key] = int(flag_counts.get(key, 0)) + 1
        missing_pattern_counts = getattr(self._owner, "_quality_shadow_missing_pattern_counts")
        lang_counts = missing_pattern_counts.setdefault(normalized_language, {})
        for pattern in getattr(result, "missing_patterns", ()):
            key = str(pattern).strip()
            if key == "":
                continue
            lang_counts[key] = int(lang_counts.get(key, 0)) + 1

    def _should_sample(self, *, job: FileEnrichJobDTO, language: str) -> bool:
        sample_rate = float(getattr(self._owner, "_quality_shadow_sample_rate", 0.0))
        if sample_rate <= 0.0:
            return False
        if sample_rate >= 1.0:
            return True
        raw = f"{language}|{job.repo_root}|{job.relative_path}|{job.content_hash}".encode("utf-8", errors="ignore")
        bucket = int(hashlib.sha1(raw).hexdigest()[:8], 16) / float(0xFFFFFFFF)
        return bucket < sample_rate
