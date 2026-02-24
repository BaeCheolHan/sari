"""L3 tree-sitter 경량 전처리 서비스."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum

from .l3_asset_loader import L3AssetLoader
from .l3_language_processor import L3LowConfidenceContext
from .l3_language_processor_registry import L3LanguageProcessorRegistry
from .l3_tree_sitter_outline import TreeSitterOutlineExtractor, TreeSitterOutlineResult

log = logging.getLogger(__name__)

class L3PreprocessDecision(str, Enum):
    """L3 전처리 이후 LSP 진입 결정을 표현한다."""

    L3_ONLY = "l3_only"
    NEEDS_L5 = "needs_l5"
    DEFERRED_HEAVY = "deferred_heavy"


@dataclass(frozen=True)
class L3PreprocessResultDTO:
    """L3 전처리 결과."""

    symbols: list[dict[str, object]]
    degraded: bool
    decision: L3PreprocessDecision
    source: str
    reason: str


class L3TreeSitterPreprocessService:
    """tree-sitter 경량 전처리(없으면 regex fallback) 서비스."""

    # TSLS(typescript-language-server) 그룹은 L3 파싱을 건너뛰고 L5로 빠르게 위임한다.
    # Vue(.vue)는 별도 경로(L3->L4->L5)를 유지해야 하므로 제외한다.
    _TSLS_FAST_PATH_EXTENSIONS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

    _PATTERN_TEXTS: dict[str, tuple[tuple[str, str], ...]] = {
        "py": (
            (r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\(|:)", "class"),
            (r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", "function"),
        ),
        "ts": (
            (r"^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\b", "class"),
            (r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", "function"),
        ),
        "javascript": (
            (r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)\b", "class"),
            (r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", "function"),
        ),
        "java": (
            (r"^\s*(?:public\s+)?(?:abstract\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\b", "class"),
            (r"^\s*(?:public|protected|private)?\s*(?:static\s+)?[A-Za-z_<>\[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", "method"),
        ),
    }
    _IMPORT_LIKE = re.compile(r"^\s*(?:import|from\s+\S+\s+import|using|use|require\(|#include)\b", re.MULTILINE)
    _CROSS_FILE_HINT = re.compile(r"\b(?:extends|implements|::|->|\.)\b")

    def __init__(
        self,
        *,
        query_compile_cache_enabled: bool = True,
        query_compile_ms_budget: float = 10.0,
        query_budget_ms: float = 30.0,
        tree_sitter_enabled: bool = True,
        asset_loader: L3AssetLoader | None = None,
        asset_mode: str = "shadow",
        asset_lang_allowlist: tuple[str, ...] = (),
        tree_sitter_outline_extractor: TreeSitterOutlineExtractor | None = None,
        language_registry: L3LanguageProcessorRegistry | None = None,
    ) -> None:
        self._query_compile_cache_enabled = bool(query_compile_cache_enabled)
        self._query_compile_ms_budget_sec = max(0.0001, float(query_compile_ms_budget)) / 1000.0
        self._query_budget_sec = max(0.0001, float(query_budget_ms)) / 1000.0
        self._pattern_cache: dict[tuple[str, str, str], tuple[tuple[re.Pattern[str], str], ...]] = {}
        self._tree_sitter_enabled = bool(tree_sitter_enabled)
        self._tree_sitter_outline_extractor = tree_sitter_outline_extractor or TreeSitterOutlineExtractor(
            asset_loader=asset_loader,
            asset_mode=asset_mode,
            asset_lang_allowlist=asset_lang_allowlist,
        )
        self._language_registry = language_registry or L3LanguageProcessorRegistry()

    def preprocess(self, *, relative_path: str, content_text: str, max_bytes: int = 262_144) -> L3PreprocessResultDTO:
        started_at = time.perf_counter()
        if self._is_tsls_fast_path(relative_path=relative_path):
            return L3PreprocessResultDTO(
                symbols=[],
                degraded=False,
                decision=L3PreprocessDecision.NEEDS_L5,
                source="none",
                reason="l3_preprocess_tsls_fast_path",
            )
        if len(content_text.encode("utf-8", errors="ignore")) > max_bytes:
            return L3PreprocessResultDTO(
                symbols=[],
                degraded=True,
                decision=L3PreprocessDecision.DEFERRED_HEAVY,
                source="none",
                reason="l3_preprocess_large_file",
            )

        language_processor = self._language_registry.resolve(relative_path=relative_path)
        pattern_key = language_processor.pattern_key(relative_path=relative_path)

        tree_sitter_result = self._try_tree_sitter_outline(pattern_key=pattern_key, content_text=content_text) if pattern_key else None
        tree_sitter_degraded_reason: str | None = None
        if tree_sitter_result is not None:
            if tree_sitter_result.degraded:
                tree_sitter_degraded_reason = tree_sitter_result.reason or "tree_sitter_degraded"
            elif len(tree_sitter_result.symbols) == 0:
                tree_sitter_degraded_reason = "l3_preprocess_no_symbols"
            elif self._needs_l5_by_low_confidence(relative_path=relative_path, content_text=content_text, symbols=tree_sitter_result.symbols):
                return L3PreprocessResultDTO(
                    symbols=tree_sitter_result.symbols,
                    degraded=False,
                    decision=L3PreprocessDecision.NEEDS_L5,
                    source="tree_sitter_outline",
                    reason="l3_preprocess_low_confidence",
                )
            else:
                return L3PreprocessResultDTO(
                    symbols=tree_sitter_result.symbols,
                    degraded=False,
                    decision=L3PreprocessDecision.L3_ONLY,
                    source="tree_sitter_outline",
                    reason="l3_preprocess_tree_sitter_only",
                )

        if pattern_key is None or pattern_key not in self._PATTERN_TEXTS:
            return L3PreprocessResultDTO(
                symbols=[],
                degraded=tree_sitter_degraded_reason is not None,
                decision=L3PreprocessDecision.NEEDS_L5,
                source="none",
                reason=tree_sitter_degraded_reason or "l3_preprocess_unsupported_language",
            )
        patterns = self._get_patterns_for_ext(pattern_key=pattern_key)
        if patterns is None:
            return L3PreprocessResultDTO(
                symbols=[],
                degraded=True,
                decision=L3PreprocessDecision.NEEDS_L5,
                source="regex_outline",
                reason=tree_sitter_degraded_reason or "l3_query_compile_budget_exceeded",
            )

        symbols: list[dict[str, object]] = []
        for pattern, kind in patterns:
            if (time.perf_counter() - started_at) > self._query_budget_sec:
                return L3PreprocessResultDTO(
                    symbols=symbols,
                    degraded=True,
                    decision=L3PreprocessDecision.NEEDS_L5,
                    source="regex_outline",
                    reason=tree_sitter_degraded_reason or "l3_query_budget_exceeded",
                )
            symbols.extend(self._extract(pattern, content_text, kind=kind))

        if len(symbols) == 0:
            return L3PreprocessResultDTO(
                symbols=[],
                degraded=tree_sitter_degraded_reason is not None,
                decision=L3PreprocessDecision.NEEDS_L5,
                source="regex_outline",
                reason=tree_sitter_degraded_reason or "l3_preprocess_no_symbols",
            )
        if self._needs_l5_by_low_confidence(
            relative_path=relative_path,
            content_text=content_text,
            symbols=symbols,
        ):
            return L3PreprocessResultDTO(
                symbols=symbols,
                degraded=tree_sitter_degraded_reason is not None,
                decision=L3PreprocessDecision.NEEDS_L5,
                source="regex_outline",
                reason=tree_sitter_degraded_reason or "l3_preprocess_low_confidence",
            )
        return L3PreprocessResultDTO(
            symbols=symbols,
            degraded=tree_sitter_degraded_reason is not None,
            decision=L3PreprocessDecision.L3_ONLY,
            source="regex_outline",
            reason=tree_sitter_degraded_reason or "l3_preprocess_only",
        )

    def _try_tree_sitter_outline(self, *, pattern_key: str, content_text: str):
        if not self._tree_sitter_enabled:
            return None
        extractor = self._tree_sitter_outline_extractor
        checker = getattr(extractor, "is_available_for", None)
        if not callable(checker) or not bool(checker(pattern_key)):
            return None
        extract = getattr(extractor, "extract_outline", None)
        if not callable(extract):
            return None
        try:
            return extract(
                lang_key=pattern_key,
                content_text=content_text,
                budget_sec=self._query_budget_sec,
            )
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            log.debug(
                "L3 tree-sitter outline extraction failed; fallback to regex path (pattern_key=%s)",
                pattern_key,
                exc_info=True,
            )
            return TreeSitterOutlineResult(
                symbols=[],
                degraded=True,
                reason=f"tree_sitter_outline_exception:{type(exc).__name__}",
            )

    def _needs_l5_by_low_confidence(self, *, relative_path: str, content_text: str, symbols: list[dict[str, object]]) -> bool:
        language_processor = self._language_registry.resolve(relative_path=relative_path)
        has_import_like = self._IMPORT_LIKE.search(content_text) is not None
        has_cross_file_hint = self._CROSS_FILE_HINT.search(content_text) is not None
        context = L3LowConfidenceContext(
            relative_path=relative_path,
            content_text=content_text,
            symbol_count=len(symbols),
            has_import_like=has_import_like,
            has_cross_file_hint=has_cross_file_hint,
        )
        return language_processor.should_route_to_l5(context=context)

    def _get_patterns_for_ext(self, *, pattern_key: str) -> tuple[tuple[re.Pattern[str], str], ...] | None:
        pattern_texts = self._PATTERN_TEXTS.get(pattern_key)
        if pattern_texts is None:
            return None
        grammar_version = "regex_grammar_v1"
        query_source = "|".join(f"{kind}:{pattern}" for pattern, kind in pattern_texts)
        query_version = hashlib.sha1(query_source.encode("utf-8")).hexdigest()
        cache_key = (pattern_key, grammar_version, query_version)
        if self._query_compile_cache_enabled:
            cached = self._pattern_cache.get(cache_key)
            if cached is not None:
                return cached
        compile_started_at = time.perf_counter()
        compiled = tuple((re.compile(pattern, re.MULTILINE), kind) for pattern, kind in pattern_texts)
        if (time.perf_counter() - compile_started_at) > self._query_compile_ms_budget_sec:
            return None
        if self._query_compile_cache_enabled:
            self._pattern_cache[cache_key] = compiled
        return compiled

    def _extract(self, pattern: re.Pattern[str], text: str, *, kind: str) -> list[dict[str, object]]:
        lines = text.splitlines()
        output: list[dict[str, object]] = []
        for match in pattern.finditer(text):
            name = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            end_line = min(len(lines), line)
            output.append(
                {
                    "name": name,
                    "kind": kind,
                    "line": line,
                    "end_line": end_line,
                    "symbol_key": f"{name}:{line}",
                    "parent_symbol_key": None,
                    "depth": 0,
                    "container_name": None,
                }
            )
        return output

    def _is_tsls_fast_path(self, *, relative_path: str) -> bool:
        lowered = relative_path.lower()
        if lowered.endswith(".vue"):
            return False
        return lowered.endswith(self._TSLS_FAST_PATH_EXTENSIONS)
