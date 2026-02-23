"""L3 scope/language 해석 보조 서비스."""

from __future__ import annotations

from solidlsp.ls_config import Language

from sari.core.language_registry import resolve_language_from_path


class L3ScopeResolutionService:
    """L3 job에서 사용할 언어 해석 책임을 분리한다."""

    def resolve_language(self, relative_path: str) -> Language | None:
        return resolve_language_from_path(file_path=relative_path)

