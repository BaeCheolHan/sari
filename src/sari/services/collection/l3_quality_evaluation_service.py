"""L3(AST) vs LSP 품질 비교(Shadow) 서비스."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .l3_asset_loader import L3AssetLoader


@dataclass(frozen=True)
class _NormalizedSymbol:
    """비교용 정규화 심볼."""

    name: str
    kind_bucket: str
    kind_raw: str
    line: int
    end_line: int


@dataclass(frozen=True)
class L3QualityEvalResultDTO:
    """AST/LSP 품질 비교 결과(proxy metric)."""

    symbol_recall_proxy: float
    symbol_precision_proxy: float
    kind_match_rate: float
    position_match_rate: float
    position_match_rate_relaxed: float
    ast_symbol_count: int
    lsp_symbol_count: int
    quality_flags: tuple[str, ...]
    missing_patterns: tuple[str, ...] = ()


class L3QualityEvaluationService:
    """정규화된 심볼 proxy 비교로 품질 지표를 계산한다."""
    _JS_CALLBACK_NAME_PATTERN = re.compile(r"^(?:.+\.)?([A-Za-z_$][A-Za-z0-9_$]*)\(\)\s+callback$")
    _JAVA_SYNTHETIC_CLASS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*Builder(?:Impl)?$")

    def __init__(
        self,
        *,
        line_tolerance: int = 2,
        asset_loader: L3AssetLoader | None = None,
    ) -> None:
        self._line_tolerance = max(0, int(line_tolerance))
        self._asset_loader = asset_loader or L3AssetLoader()

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
        ast = self._dedupe_symbols_for_proxy(language=language, symbols=ast)
        lsp = self._dedupe_symbols_for_proxy(language=language, symbols=lsp)

        ast_count = len(ast)
        lsp_count = len(lsp)
        if ast_count == 0 and lsp_count == 0:
            return L3QualityEvalResultDTO(
                symbol_recall_proxy=1.0,
                symbol_precision_proxy=1.0,
                kind_match_rate=1.0,
                position_match_rate=1.0,
                position_match_rate_relaxed=1.0,
                ast_symbol_count=0,
                lsp_symbol_count=0,
                quality_flags=(),
            )

        matched_ast_indices: set[int] = set()
        matched_lsp_indices: set[int] = set()
        kind_matches = 0
        position_matches = 0
        position_matches_relaxed = 0

        for ast_idx, ast_sym in enumerate(ast):
            best_idx = self._find_match(
                language=language,
                ast_sym=ast_sym,
                lsp_symbols=lsp,
                matched_lsp_indices=matched_lsp_indices,
            )
            if best_idx is None:
                continue
            matched_ast_indices.add(ast_idx)
            matched_lsp_indices.add(best_idx)
            lsp_sym = lsp[best_idx]
            if ast_sym.kind_bucket == lsp_sym.kind_bucket:
                kind_matches += 1
            line_gap = abs(ast_sym.line - lsp_sym.line)
            if line_gap <= self._line_tolerance:
                position_matches += 1
            relaxed_gap = self._resolve_relaxed_line_gap(language=language, kind_bucket=ast_sym.kind_bucket)
            if line_gap <= relaxed_gap:
                position_matches_relaxed += 1

        match_count = len(matched_ast_indices)
        ignored_lsp_indices = self._collect_ignored_lsp_indices(
            language=language,
            lsp_symbols=lsp,
            matched_lsp_indices=matched_lsp_indices,
        )
        effective_lsp_count = max(0, lsp_count - len(ignored_lsp_indices))
        recall = 1.0 if effective_lsp_count == 0 else float(match_count) / float(effective_lsp_count)
        precision = 1.0 if ast_count == 0 else float(match_count) / float(ast_count)
        kind_match_rate = 1.0 if match_count == 0 else float(kind_matches) / float(match_count)
        position_match_rate = 1.0 if match_count == 0 else float(position_matches) / float(match_count)
        position_match_rate_relaxed = (
            1.0 if match_count == 0 else float(position_matches_relaxed) / float(match_count)
        )

        flags: list[str] = []
        if effective_lsp_count > ast_count:
            flags.append("ast_missing_symbols")
        if ast_count > lsp_count:
            flags.append("ast_extra_symbols")
        if match_count > 0 and kind_matches < match_count:
            flags.append("kind_mismatch_present")
        if match_count > 0 and position_matches < match_count:
            flags.append("position_mismatch_present")
        if effective_lsp_count > 0 and match_count == 0:
            flags.append("no_proxy_matches")
        missing_patterns = self._collect_missing_patterns(
            language=language,
            lsp_symbols=lsp,
            matched_lsp_indices=matched_lsp_indices,
            ignored_lsp_indices=ignored_lsp_indices,
        )

        return L3QualityEvalResultDTO(
            symbol_recall_proxy=recall,
            symbol_precision_proxy=precision,
            kind_match_rate=kind_match_rate,
            position_match_rate=position_match_rate,
            position_match_rate_relaxed=position_match_rate_relaxed,
            ast_symbol_count=ast_count,
            lsp_symbol_count=lsp_count,
            quality_flags=tuple(flags),
            missing_patterns=tuple(missing_patterns),
        )

    def _find_match(
        self,
        *,
        language: str,
        ast_sym: _NormalizedSymbol,
        lsp_symbols: list[_NormalizedSymbol],
        matched_lsp_indices: set[int],
    ) -> int | None:
        lang = str(language).strip().lower()
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
        if best_idx is not None:
            return best_idx

        line_override = self._asset_loader.load(lang).line_match_overrides
        fallback_buckets = self._read_str_list(line_override.get("name_kind_fallback_buckets"))
        fallback_max_gap = self._read_non_negative_int(line_override.get("name_kind_fallback_max_line_gap"))
        if ast_sym.kind_bucket in fallback_buckets:
            fallback_best_idx: int | None = None
            fallback_best_gap: int | None = None
            for idx, candidate in enumerate(lsp_symbols):
                if idx in matched_lsp_indices:
                    continue
                if candidate.name != ast_sym.name:
                    continue
                if candidate.kind_bucket != ast_sym.kind_bucket:
                    continue
                gap = abs(candidate.line - ast_sym.line)
                if fallback_max_gap is not None and gap > fallback_max_gap:
                    continue
                if fallback_best_gap is None or gap < fallback_best_gap:
                    fallback_best_gap = gap
                    fallback_best_idx = idx
            return fallback_best_idx
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
        normalized_name = self._normalize_name(language=language, name=name)
        if normalized_name == "":
            return None
        return _NormalizedSymbol(
            name=normalized_name,
            kind_bucket=self._kind_bucket(language=language, kind=kind_raw),
            kind_raw=kind_raw,
            line=line,
            end_line=end_line,
        )

    def _collect_missing_patterns(
        self,
        *,
        language: str,
        lsp_symbols: list[_NormalizedSymbol],
        matched_lsp_indices: set[int],
        ignored_lsp_indices: set[int] | None = None,
    ) -> list[str]:
        out: list[str] = []
        ignored = ignored_lsp_indices or set()
        for idx, symbol in enumerate(lsp_symbols):
            if idx in matched_lsp_indices:
                continue
            if idx in ignored:
                continue
            out.append(self._missing_pattern(language=language, symbol=symbol))
        return out

    def _collect_ignored_lsp_indices(
        self,
        *,
        language: str,
        lsp_symbols: list[_NormalizedSymbol],
        matched_lsp_indices: set[int],
    ) -> set[int]:
        lang = str(language).strip().lower()
        if lang != "java":
            return set()
        ignored: set[int] = set()
        for idx, symbol in enumerate(lsp_symbols):
            if idx in matched_lsp_indices:
                continue
            if self._JAVA_SYNTHETIC_CLASS_PATTERN.match(symbol.name):
                ignored.add(idx)
        return ignored

    def _dedupe_symbols_for_proxy(
        self,
        *,
        language: str,
        symbols: list[_NormalizedSymbol],
    ) -> list[_NormalizedSymbol]:
        lang = str(language).strip().lower()
        if lang != "java":
            return symbols
        # Java LSP can emit duplicate field symbols (e.g., Lombok builder context)
        # for the same identifier. Keep one to avoid penalizing recall with synthetic duplicates.
        seen_field_names: set[str] = set()
        out: list[_NormalizedSymbol] = []
        for symbol in symbols:
            if symbol.kind_bucket == "field":
                if symbol.name in seen_field_names:
                    continue
                seen_field_names.add(symbol.name)
            out.append(symbol)
        return out

    def _missing_pattern(self, *, language: str, symbol: _NormalizedSymbol) -> str:
        lang = str(language).strip().lower()
        bundle = self._asset_loader.load(lang)
        for rule in bundle.missing_pattern_rules:
            if self._match_missing_rule(rule=rule, symbol=symbol):
                result = rule.get("result")
                if isinstance(result, str) and result.strip() != "":
                    return result.strip()
        if lang != "java":
            return f"missing_{symbol.kind_bucket}"
        raw = symbol.kind_raw
        if raw in {"9", "constructor", "constructor_declaration"}:
            return "missing_constructor"
        if symbol.kind_bucket == "field":
            return "missing_field"
        if symbol.kind_bucket in {"class", "interface", "enum"} and ("." in symbol.name or "$" in symbol.name):
            return "missing_nested_type"
        return f"missing_{symbol.kind_bucket}"

    def _normalize_name(self, *, language: str, name: str) -> str:
        out = name.strip()
        if out == "":
            return ""
        lang = str(language).strip().lower()
        if lang == "javascript":
            if len(out) >= 2 and out[0] == out[-1] and out[0] in {"'", '"', "`"}:
                out = out[1:-1].strip()
            if len(out) >= 2 and out[0] == "[" and out[-1] == "]":
                out = out[1:-1].strip()
            if out == "<function>":
                return ""
            callback_match = self._JS_CALLBACK_NAME_PATTERN.match(out)
            if callback_match is not None:
                return callback_match.group(1)
            return out
        if lang != "java":
            return out
        if out.startswith("new "):
            return ""
        if "$value" in out or "$set" in out:
            return ""
        if "(" in out and out.endswith(")"):
            out = out.split("(", 1)[0].strip()
        generic_idx = out.find("<")
        if generic_idx > 0:
            out = out[:generic_idx].strip()
        return out

    def _kind_bucket(self, *, language: str, kind: str) -> str:
        lang = str(language).strip().lower()
        bundle = self._asset_loader.load(lang)
        mapped = bundle.kind_bucket_map.get(kind)
        if mapped is not None and mapped.strip() != "":
            return mapped.strip()
        lsp_numeric = {
            "2": "module",      # SymbolKind.Module
            "4": "module",      # SymbolKind.Package -> bucket as module
            "5": "class",       # SymbolKind.Class
            "6": "method",      # SymbolKind.Method
            "7": "field",       # SymbolKind.Property -> bucket as field
            "8": "field",       # SymbolKind.Field
            "9": "method",      # SymbolKind.Constructor -> bucket as method
            "10": "enum",       # SymbolKind.Enum
            "11": "interface",  # SymbolKind.Interface
            "12": "function",   # SymbolKind.Function
            "13": "variable",   # SymbolKind.Variable
            "14": "field",      # SymbolKind.Constant
            "22": "field",      # SymbolKind.EnumMember
        }
        if kind in lsp_numeric:
            return lsp_numeric[kind]
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
            "variable": "field",
            "property": "field",
        }
        if lang in {"typescript", "ts", "javascript", "js", "vue"} and kind in ts_aliases:
            return ts_aliases[kind]
        if lang in {"java"} and kind in java_aliases:
            return java_aliases[kind]
        return "other"

    def _read_str_list(self, raw: object) -> tuple[str, ...]:
        if not isinstance(raw, list):
            return ()
        out: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            normalized = item.strip()
            if normalized == "":
                continue
            out.append(normalized)
        return tuple(out)

    def _read_non_negative_int(self, raw: object) -> int | None:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if value < 0:
            return None
        return value

    def _resolve_relaxed_line_gap(self, *, language: str, kind_bucket: str) -> int:
        base = self._line_tolerance
        lang = str(language).strip().lower()
        line_override = self._asset_loader.load(lang).line_match_overrides
        fallback_buckets = self._read_str_list(line_override.get("name_kind_fallback_buckets"))
        if kind_bucket not in fallback_buckets:
            return base
        max_gap = self._read_non_negative_int(line_override.get("name_kind_fallback_max_line_gap"))
        if max_gap is None:
            return base
        return max(base, max_gap)

    def _match_missing_rule(self, *, rule: dict[str, object], symbol: _NormalizedSymbol) -> bool:
        kind_bucket_in = self._read_str_list(rule.get("when_kind_bucket_in"))
        if kind_bucket_in and symbol.kind_bucket not in kind_bucket_in:
            return False
        kind_raw_in = self._read_str_list(rule.get("when_kind_raw_in"))
        if kind_raw_in and symbol.kind_raw not in kind_raw_in:
            return False
        name_contains = self._read_str_list(rule.get("when_name_contains_any"))
        if name_contains and not any(token in symbol.name for token in name_contains):
            return False
        return True
    _JS_CALLBACK_NAME_PATTERN = re.compile(r"^(?:.+\.)?([A-Za-z_$][A-Za-z0-9_$]*)\(\)\s+callback$")
