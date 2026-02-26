"""LSP raw symbol 목록을 도구 저장 형식으로 정규화한다."""

from __future__ import annotations


class LspSymbolNormalizerService:
    """extract_once 심볼 정규화 루프 책임 분리."""

    def __init__(
        self,
        *,
        normalize_location,
        build_symbol_key,
        resolve_symbol_depth,
        resolve_container_name,
    ) -> None:
        self._normalize_location = normalize_location
        self._build_symbol_key = build_symbol_key
        self._resolve_symbol_depth = resolve_symbol_depth
        self._resolve_container_name = resolve_container_name

    def normalize_symbols(
        self,
        *,
        repo_root: str,
        normalized_relative_path: str,
        raw_symbols: list[object],
    ) -> list[dict[str, object]]:
        symbols: list[dict[str, object]] = []
        for raw in raw_symbols:
            if not isinstance(raw, dict):
                continue
            location = raw.get("location")
            resolved_relative_path = normalized_relative_path
            if isinstance(location, dict):
                resolved_relative_path = self._normalize_location(
                    location=location,
                    fallback_relative_path=normalized_relative_path,
                    repo_root=repo_root,
                )
            location = raw.get("location")
            if not isinstance(location, dict):
                location = {}
            range_data = location.get("range")
            line = 0
            end_line = 0
            if isinstance(range_data, dict):
                start_data = range_data.get("start")
                end_data = range_data.get("end")
                if isinstance(start_data, dict):
                    line = int(start_data.get("line", 0))
                if isinstance(end_data, dict):
                    end_line = int(end_data.get("line", line))
            symbol_name = str(raw.get("name", ""))
            symbol_kind = str(raw.get("kind", ""))
            parent_symbol = raw.get("parent")
            parent_symbol_key = self._build_symbol_key(
                repo_root=repo_root,
                relative_path=resolved_relative_path,
                symbol=parent_symbol,
                fallback_parent_key=None,
            )
            symbol_key = self._build_symbol_key(
                repo_root=repo_root,
                relative_path=resolved_relative_path,
                symbol=raw,
                fallback_parent_key=parent_symbol_key,
            )
            symbols.append(
                {
                    "name": symbol_name,
                    "kind": symbol_kind,
                    "line": line,
                    "end_line": end_line,
                    "symbol_key": symbol_key,
                    "parent_symbol_key": parent_symbol_key,
                    "depth": self._resolve_symbol_depth(raw),
                    "container_name": self._resolve_container_name(raw),
                }
            )
        return symbols
