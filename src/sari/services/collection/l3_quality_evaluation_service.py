"""L3(AST) vs LSP 품질 비교(Shadow) 서비스."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _NormalizedSymbol:
    """비교용 정규화 심볼."""

    name: str
    kind_bucket: str
    line: int
    end_line: int


@dataclass(frozen=True)
class L3QualityEvalResultDTO:
    """AST/LSP 품질 비교 결과(proxy metric)."""

    symbol_recall_proxy: float
    symbol_precision_proxy: float
    kind_match_rate: float
    position_match_rate: float
    ast_symbol_count: int
    lsp_symbol_count: int
    quality_flags: tuple[str, ...]


class L3QualityEvaluationService:
    """정규화된 심볼 proxy 비교로 품질 지표를 계산한다."""

    def __init__(self, *, line_tolerance: int = 2) -> None:
        self._line_tolerance = max(0, int(line_tolerance))

    def evaluate(
        self,
        *,
        language: str,
        ast_symbols: list[dict[str, object]],
        lsp_symbols: list[dict[str, object]],
    ) -> L3QualityEvalResultDTO:
        ast = [self._normalize_symbol(language=language, raw=item) for item in ast_symbols]
        lsp = [self._normalize_symbol(language=language, raw=item) for item in lsp_symbols]
        ast = [item for item in ast if item is not None]
        lsp = [item for item in lsp if item is not None]

        ast_count = len(ast)
        lsp_count = len(lsp)
        if ast_count == 0 and lsp_count == 0:
            return L3QualityEvalResultDTO(
                symbol_recall_proxy=1.0,
                symbol_precision_proxy=1.0,
                kind_match_rate=1.0,
                position_match_rate=1.0,
                ast_symbol_count=0,
                lsp_symbol_count=0,
                quality_flags=(),
            )

        matched_ast_indices: set[int] = set()
        matched_lsp_indices: set[int] = set()
        kind_matches = 0
        position_matches = 0

        for ast_idx, ast_sym in enumerate(ast):
            best_idx = self._find_match(ast_sym=ast_sym, lsp_symbols=lsp, matched_lsp_indices=matched_lsp_indices)
            if best_idx is None:
                continue
            matched_ast_indices.add(ast_idx)
            matched_lsp_indices.add(best_idx)
            lsp_sym = lsp[best_idx]
            if ast_sym.kind_bucket == lsp_sym.kind_bucket:
                kind_matches += 1
            if abs(ast_sym.line - lsp_sym.line) <= self._line_tolerance:
                position_matches += 1

        match_count = len(matched_ast_indices)
        recall = 1.0 if lsp_count == 0 else float(match_count) / float(lsp_count)
        precision = 1.0 if ast_count == 0 else float(match_count) / float(ast_count)
        kind_match_rate = 1.0 if match_count == 0 else float(kind_matches) / float(match_count)
        position_match_rate = 1.0 if match_count == 0 else float(position_matches) / float(match_count)

        flags: list[str] = []
        if lsp_count > ast_count:
            flags.append("ast_missing_symbols")
        if ast_count > lsp_count:
            flags.append("ast_extra_symbols")
        if match_count > 0 and kind_matches < match_count:
            flags.append("kind_mismatch_present")
        if match_count > 0 and position_matches < match_count:
            flags.append("position_mismatch_present")
        if lsp_count > 0 and match_count == 0:
            flags.append("no_proxy_matches")

        return L3QualityEvalResultDTO(
            symbol_recall_proxy=recall,
            symbol_precision_proxy=precision,
            kind_match_rate=kind_match_rate,
            position_match_rate=position_match_rate,
            ast_symbol_count=ast_count,
            lsp_symbol_count=lsp_count,
            quality_flags=tuple(flags),
        )

    def _find_match(
        self,
        *,
        ast_sym: _NormalizedSymbol,
        lsp_symbols: list[_NormalizedSymbol],
        matched_lsp_indices: set[int],
    ) -> int | None:
        best_idx: int | None = None
        best_score = -1
        for idx, candidate in enumerate(lsp_symbols):
            if idx in matched_lsp_indices:
                continue
            if candidate.name != ast_sym.name:
                continue
            if abs(candidate.line - ast_sym.line) > self._line_tolerance:
                continue
            score = 0
            if candidate.kind_bucket == ast_sym.kind_bucket:
                score += 2
            line_gap = abs(candidate.line - ast_sym.line)
            score += max(0, self._line_tolerance - line_gap)
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx

    def _normalize_symbol(self, *, language: str, raw: dict[str, object]) -> _NormalizedSymbol | None:
        try:
            name = str(raw.get("name", "")).strip()
            if name == "":
                return None
            kind_raw = str(raw.get("kind", "other")).strip().lower()
            line = int(raw.get("line", 0))
            end_line = int(raw.get("end_line", line if line > 0 else 0))
        except (TypeError, ValueError):
            return None
        if line <= 0:
            line = 0
        if end_line < line:
            end_line = line
        return _NormalizedSymbol(
            name=self._normalize_name(name),
            kind_bucket=self._kind_bucket(language=language, kind=kind_raw),
            line=line,
            end_line=end_line,
        )

    def _normalize_name(self, name: str) -> str:
        return name.strip()

    def _kind_bucket(self, *, language: str, kind: str) -> str:
        lang = str(language).strip().lower()
        direct = {
            "class": "class",
            "method": "method",
            "function": "function",
            "field": "field",
            "variable": "variable",
            "interface": "interface",
            "enum": "enum",
            "module": "module",
        }
        if kind in direct:
            return direct[kind]
        ts_aliases = {
            "method_definition": "method",
            "function_declaration": "function",
            "lexical_declaration": "variable",
            "property_definition": "field",
        }
        java_aliases = {
            "constructor": "method",
            "record": "class",
            "enum_constant": "field",
        }
        if lang in {"typescript", "ts", "javascript", "js", "vue"} and kind in ts_aliases:
            return ts_aliases[kind]
        if lang in {"java"} and kind in java_aliases:
            return java_aliases[kind]
        return "other"

