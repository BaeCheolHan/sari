"""L3 degraded 시 cheap fallback(정규식/라인스캔) 서비스."""

from __future__ import annotations

import re

from .l3_treesitter_preprocess_service import L3PreprocessDecision, L3PreprocessResultDTO


class L3DegradedFallbackService:
    """tree-sitter 없이 헤더 수준 심볼만 추출한다."""

    _PRESET_PATTERNS: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
        "py": (
            (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|:)", re.MULTILINE), "class"),
            (re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE), "function"),
        ),
        "ts": (
            (re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE), "class"),
            (
                re.compile(
                    r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                    re.MULTILINE,
                ),
                "function",
            ),
        ),
        "java": (
            (re.compile(r"^\s*(?:public\s+)?(?:abstract\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE), "class"),
            (
                re.compile(
                    r"^\s*(?:public|protected|private)?\s*(?:static\s+)?[A-Za-z_<>\[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                    re.MULTILINE,
                ),
                "method",
            ),
        ),
    }
    _EXT_TO_PRESET = {
        "py": "py",
        "ts": "ts",
        "tsx": "ts",
        "js": "ts",
        "jsx": "ts",
        "vue": "ts",
        "java": "java",
    }

    def fallback(self, *, relative_path: str, content_text: str, max_symbols: int = 64) -> L3PreprocessResultDTO:
        preset = self._resolve_preset(relative_path=relative_path)
        if preset is None:
            return L3PreprocessResultDTO(
                symbols=[],
                degraded=True,
                decision=L3PreprocessDecision.L3_ONLY,
                source="regex_fallback",
                reason="l3_degraded_fallback_unsupported_preset",
            )
        symbols: list[dict[str, object]] = []
        for pattern, kind in self._PRESET_PATTERNS[preset]:
            for match in pattern.finditer(content_text):
                line = content_text.count("\n", 0, match.start()) + 1
                symbols.append(
                    {
                        "name": match.group(1),
                        "kind": kind,
                        "line": line,
                        "end_line": line,
                        "symbol_key": f"{match.group(1)}:{line}",
                        "parent_symbol_key": None,
                        "depth": 0,
                        "container_name": None,
                    }
                )
                if len(symbols) >= max_symbols:
                    break
            if len(symbols) >= max_symbols:
                break
        return L3PreprocessResultDTO(
            symbols=symbols,
            degraded=True,
            decision=L3PreprocessDecision.L3_ONLY,
            source="regex_fallback",
            reason="l3_degraded_fallback",
        )

    def _resolve_preset(self, *, relative_path: str) -> str | None:
        if "." not in relative_path:
            return None
        ext = relative_path.rsplit(".", 1)[-1].lower()
        return self._EXT_TO_PRESET.get(ext)
