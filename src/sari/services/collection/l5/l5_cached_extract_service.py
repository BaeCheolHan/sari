"""L5 extract 호출 전 DB short-circuit를 담당한다."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from sari.services.lsp_extraction_contracts import LspExtractionResultDTO


@dataclass(frozen=True)
class L5CachedExtractMetrics:
    lookup_count: int = 0
    hit_count: int = 0
    skipped_lsp_count: int = 0
    miss_reason_no_l5_semantics: int = 0
    miss_reason_no_symbols: int = 0
    miss_reason_scope_ambiguous: int = 0
    miss_reason_error_fallback: int = 0


class L5CachedExtractService:
    """content_hash 기준으로 L5(LSP) 호출을 DB hit 시 생략한다."""

    def __init__(
        self,
        *,
        tool_layer_repo: object | None,
        lsp_repo: object | None,
        delegate_extract: object,
        enabled: bool,
        log_miss_reason: bool,
    ) -> None:
        self._tool_layer_repo = tool_layer_repo
        self._lsp_repo = lsp_repo
        self._delegate_extract = delegate_extract
        self._enabled = bool(enabled)
        self._log_miss_reason = bool(log_miss_reason)
        self._metrics = L5CachedExtractMetrics()

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        self._metrics = L5CachedExtractMetrics(
            lookup_count=self._metrics.lookup_count + 1,
            hit_count=self._metrics.hit_count,
            skipped_lsp_count=self._metrics.skipped_lsp_count,
            miss_reason_no_l5_semantics=self._metrics.miss_reason_no_l5_semantics,
            miss_reason_no_symbols=self._metrics.miss_reason_no_symbols,
            miss_reason_scope_ambiguous=self._metrics.miss_reason_scope_ambiguous,
            miss_reason_error_fallback=self._metrics.miss_reason_error_fallback,
        )
        if not self._enabled:
            return self._delegate_extract(repo_root, relative_path, content_hash)
        if self._tool_layer_repo is None or self._lsp_repo is None:
            return self._delegate_extract(repo_root, relative_path, content_hash)

        delegate_reason: str | None = None
        try:
            resolved_repo_root = self._tool_layer_repo.resolve_effective_repo_root(
                repo_root=repo_root,
                relative_path=relative_path,
                content_hash=content_hash,
            )
            if resolved_repo_root is None:
                delegate_reason = "scope_ambiguous"
            else:
                snapshot = self._tool_layer_repo.load_effective_snapshot(
                    workspace_id=repo_root.strip(),
                    repo_root=repo_root,
                    relative_path=relative_path,
                    content_hash=content_hash,
                )
                has_l5_semantics = isinstance(snapshot.get("l5"), list) and len(snapshot.get("l5", [])) > 0
                list_full_symbols = getattr(self._lsp_repo, "list_file_symbols_full", None)
                if callable(list_full_symbols):
                    symbols = list_full_symbols(
                        resolved_repo_root,
                        relative_path,
                        content_hash,
                    )
                else:
                    symbols = self._lsp_repo.list_file_symbols(
                        resolved_repo_root,
                        relative_path,
                        content_hash,
                    )
                if len(symbols) == 0:
                    delegate_reason = "no_symbols"
                else:
                    relations = self._lsp_repo.list_file_relations(
                        resolved_repo_root,
                        relative_path,
                        content_hash,
                    )
                    if not has_l5_semantics:
                        self._record_miss("no_l5_semantics")
                    self._record_hit()
                    return LspExtractionResultDTO(symbols=symbols, relations=relations, error_message=None)
        except (sqlite3.Error, RuntimeError, OSError, ValueError, TypeError):
            delegate_reason = "error_fallback"
        if delegate_reason is not None:
            self._record_miss(delegate_reason)
            return self._delegate_extract(repo_root, relative_path, content_hash)
        return self._delegate_extract(repo_root, relative_path, content_hash)

    def get_metrics(self) -> dict[str, float]:
        hit_rate = 0.0
        if self._metrics.lookup_count > 0:
            hit_rate = (float(self._metrics.hit_count) / float(self._metrics.lookup_count)) * 100.0
        return {
            "l5_db_cache_lookup_count": float(self._metrics.lookup_count),
            "l5_db_cache_hit_count": float(self._metrics.hit_count),
            "l5_db_cache_hit_rate_pct": hit_rate,
            "l5_lsp_call_skipped_by_content_hash": float(self._metrics.skipped_lsp_count),
            "l5_db_cache_miss_reason_no_l5_semantics": float(self._metrics.miss_reason_no_l5_semantics),
            "l5_db_cache_miss_reason_no_symbols": float(self._metrics.miss_reason_no_symbols),
            "l5_db_cache_miss_reason_scope_ambiguous": float(self._metrics.miss_reason_scope_ambiguous),
            "l5_db_cache_miss_reason_error_fallback": float(self._metrics.miss_reason_error_fallback),
        }

    def _record_hit(self) -> None:
        self._metrics = L5CachedExtractMetrics(
            lookup_count=self._metrics.lookup_count,
            hit_count=self._metrics.hit_count + 1,
            skipped_lsp_count=self._metrics.skipped_lsp_count + 1,
            miss_reason_no_l5_semantics=self._metrics.miss_reason_no_l5_semantics,
            miss_reason_no_symbols=self._metrics.miss_reason_no_symbols,
            miss_reason_scope_ambiguous=self._metrics.miss_reason_scope_ambiguous,
            miss_reason_error_fallback=self._metrics.miss_reason_error_fallback,
        )

    def _record_miss(self, reason: str) -> None:
        if reason == "no_l5_semantics":
            self._metrics = L5CachedExtractMetrics(
                lookup_count=self._metrics.lookup_count,
                hit_count=self._metrics.hit_count,
                skipped_lsp_count=self._metrics.skipped_lsp_count,
                miss_reason_no_l5_semantics=self._metrics.miss_reason_no_l5_semantics + 1,
                miss_reason_no_symbols=self._metrics.miss_reason_no_symbols,
                miss_reason_scope_ambiguous=self._metrics.miss_reason_scope_ambiguous,
                miss_reason_error_fallback=self._metrics.miss_reason_error_fallback,
            )
            return
        if reason == "no_symbols":
            self._metrics = L5CachedExtractMetrics(
                lookup_count=self._metrics.lookup_count,
                hit_count=self._metrics.hit_count,
                skipped_lsp_count=self._metrics.skipped_lsp_count,
                miss_reason_no_l5_semantics=self._metrics.miss_reason_no_l5_semantics,
                miss_reason_no_symbols=self._metrics.miss_reason_no_symbols + 1,
                miss_reason_scope_ambiguous=self._metrics.miss_reason_scope_ambiguous,
                miss_reason_error_fallback=self._metrics.miss_reason_error_fallback,
            )
            return
        if reason == "scope_ambiguous":
            self._metrics = L5CachedExtractMetrics(
                lookup_count=self._metrics.lookup_count,
                hit_count=self._metrics.hit_count,
                skipped_lsp_count=self._metrics.skipped_lsp_count,
                miss_reason_no_l5_semantics=self._metrics.miss_reason_no_l5_semantics,
                miss_reason_no_symbols=self._metrics.miss_reason_no_symbols,
                miss_reason_scope_ambiguous=self._metrics.miss_reason_scope_ambiguous + 1,
                miss_reason_error_fallback=self._metrics.miss_reason_error_fallback,
            )
            return
        if reason == "error_fallback":
            self._metrics = L5CachedExtractMetrics(
                lookup_count=self._metrics.lookup_count,
                hit_count=self._metrics.hit_count,
                skipped_lsp_count=self._metrics.skipped_lsp_count,
                miss_reason_no_l5_semantics=self._metrics.miss_reason_no_l5_semantics,
                miss_reason_no_symbols=self._metrics.miss_reason_no_symbols,
                miss_reason_scope_ambiguous=self._metrics.miss_reason_scope_ambiguous,
                miss_reason_error_fallback=self._metrics.miss_reason_error_fallback + 1,
            )
            return
