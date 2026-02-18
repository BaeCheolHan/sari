"""LSP 경로 정규화 유틸리티를 제공한다."""

from __future__ import annotations

from sari.core.exceptions import ErrorContext, ValidationError
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
    if value.startswith("/"):
        raise ValidationError(ErrorContext(code="ERR_URI_PATH_INVALID", message="절대 경로는 허용되지 않습니다"))
    if len(value) >= 3 and value[1] == ":" and value[2] == "/":
        raise ValidationError(ErrorContext(code="ERR_URI_PATH_INVALID", message="절대 경로는 허용되지 않습니다"))
    segments = [segment for segment in value.split("/") if segment not in ("", ".")]
    if any(segment == ".." for segment in segments):
        raise ValidationError(ErrorContext(code="ERR_URI_PATH_INVALID", message="repo 범위를 벗어나는 상대 경로입니다"))
    return "/".join(segments)


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
