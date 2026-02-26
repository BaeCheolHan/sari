"""L3 query/mapping 자산 로더."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class L3AssetBundle:
    """언어별 L3 자산 묶음."""

    language: str
    query_source: str | None
    capture_to_kind: dict[str, str]
    kind_bucket_map: dict[str, str]
    missing_pattern_rules: tuple[dict[str, object], ...]
    line_match_overrides: dict[str, object]
    name_extract_rules: tuple[dict[str, object], ...]


class L3AssetLoader:
    """자산 manifest + language mapping/query를 로드한다."""

    def __init__(self, *, assets_root: Path | None = None) -> None:
        base = (
            assets_root
            if assets_root is not None
            else Path(__file__).resolve().parent / "assets"
        )
        self._assets_root = base
        self._manifest_path = self._assets_root / "manifest.json"
        self._query_root = self._assets_root / "queries"
        self._mapping_root = self._assets_root / "mappings"
        self._cache: dict[str, L3AssetBundle] = {}
        self._last_load_error: str | None = None
        self._manifest_version = self._load_manifest_version()

    @property
    def manifest_version(self) -> str:
        return self._manifest_version

    @property
    def last_load_error(self) -> str | None:
        """최근 자산 로드 실패 사유를 반환한다."""
        return self._last_load_error

    def load(self, language: str) -> L3AssetBundle:
        normalized = self._normalize_language(language)
        cached = self._cache.get(normalized)
        if cached is not None:
            return cached
        mapping = self._load_mapping(normalized)
        bundle = L3AssetBundle(
            language=normalized,
            query_source=self._load_query_source(normalized),
            capture_to_kind=self._read_str_map(mapping.get("capture_to_kind")),
            kind_bucket_map=self._read_str_map(mapping.get("kind_bucket_map")),
            missing_pattern_rules=self._read_rule_list(mapping.get("missing_pattern_rules")),
            line_match_overrides=self._read_obj_map(mapping.get("line_match_overrides")),
            name_extract_rules=self._read_rule_list(mapping.get("name_extract_rules")),
        )
        self._cache[normalized] = bundle
        return bundle

    def _normalize_language(self, language: str) -> str:
        lowered = str(language).strip().lower()
        aliases = {
            "py": "python",
            "ts": "typescript",
            "js": "javascript",
            "jsx": "javascript",
            "mjs": "javascript",
            "cjs": "javascript",
            "vue": "typescript",
        }
        return aliases.get(lowered, lowered)

    def _load_manifest_version(self) -> str:
        try:
            payload = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            self._last_load_error = f"manifest_load_error:{type(exc).__name__}"
            log.debug("failed to load asset manifest(path=%s)", self._manifest_path, exc_info=True)
            return "unknown"
        raw = payload.get("version")
        return str(raw) if raw is not None else "unknown"

    def _load_query_source(self, language: str) -> str | None:
        query_path = self._query_root / language / "outline.scm"
        try:
            if not query_path.is_file():
                return None
            source = query_path.read_text(encoding="utf-8")
        except (OSError, ValueError, TypeError) as exc:
            self._last_load_error = f"query_load_error:{language}:{type(exc).__name__}"
            log.debug("failed to load query source(path=%s, language=%s)", query_path, language, exc_info=True)
            return None
        return source or None

    def _load_mapping(self, language: str) -> dict[str, object]:
        language_path = self._mapping_root / f"{language}.yaml"
        default_path = self._mapping_root / "default.yaml"
        payload = self._read_mapping_file(language_path)
        if payload is not None:
            return payload
        fallback = self._read_mapping_file(default_path)
        return fallback if fallback is not None else {}

    def _read_mapping_file(self, path: Path) -> dict[str, object] | None:
        try:
            if not path.is_file():
                return None
            # JSON is valid YAML; keep dependency-free loader for now.
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            self._last_load_error = f"mapping_load_error:{path.name}:{type(exc).__name__}"
            log.debug("failed to load mapping file(path=%s)", path, exc_info=True)
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _read_str_map(self, raw: object) -> dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in raw.items():
            if key is None or value is None:
                continue
            out[str(key)] = str(value)
        return out

    def _read_obj_map(self, raw: object) -> dict[str, object]:
        if not isinstance(raw, dict):
            return {}
        out: dict[str, object] = {}
        for key, value in raw.items():
            if key is None:
                continue
            out[str(key)] = value
        return out

    def _read_rule_list(self, raw: object) -> tuple[dict[str, object], ...]:
        if not isinstance(raw, list):
            return ()
        out: list[dict[str, object]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            normalized: dict[str, object] = {}
            for key, value in item.items():
                if key is None:
                    continue
                normalized[str(key)] = value
            out.append(normalized)
        return tuple(out)
