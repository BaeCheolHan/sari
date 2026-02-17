"""LSP 경로 정규화 유틸리티를 제공한다."""

from __future__ import annotations

from sari.core.exceptions import ValidationError
from sari.lsp.uri_utils import file_uri_to_repo_relative


def normalize_repo_relative_path(raw: str) -> str:
    """repo 상대 경로 포맷을 단일 규칙으로 정규화한다."""
    value = raw.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    while "//" in value:
        value = value.replace("//", "/")
    if value.endswith("/"):
        value = value[:-1]
    return value


def normalize_location_to_repo_relative(
    location: dict[str, object],
    fallback_relative_path: str,
    repo_root: str,
) -> str:
    """LSP location payload를 repo 상대 경로로 정규화한다."""
    relative_path = fallback_relative_path
    raw_relative = location.get("relativePath")
    if isinstance(raw_relative, str) and raw_relative.strip() != "":
        relative_path = raw_relative
    else:
        raw_uri = location.get("uri")
        if isinstance(raw_uri, str) and raw_uri.startswith("file://"):
            try:
                relative_path = file_uri_to_repo_relative(uri=raw_uri, repo_root=repo_root)
            except ValidationError:
                relative_path = fallback_relative_path
    return normalize_repo_relative_path(relative_path)
