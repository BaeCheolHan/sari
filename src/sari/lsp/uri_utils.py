"""LSP file URI 파싱 유틸리티를 제공한다."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlparse

from sari.core.exceptions import ErrorContext, ValidationError


def file_uri_to_repo_relative(uri: str, repo_root: str) -> str:
    """file URI를 repo 상대 경로로 변환한다."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValidationError(ErrorContext(code="ERR_URI_PATH_INVALID", message="file URI만 지원합니다"))

    decoded_path = unquote(parsed.path)
    if _is_windows_uri(parsed.path, parsed.netloc, repo_root):
        return _file_uri_to_repo_relative_windows(parsed=parsed, decoded_path=decoded_path, repo_root=repo_root)

    absolute_path = Path(decoded_path).resolve()
    repo_path = Path(repo_root).resolve()
    try:
        return str(absolute_path.relative_to(repo_path).as_posix())
    except ValueError as exc:
        raise ValidationError(ErrorContext(code="ERR_URI_PATH_INVALID", message="uri가 repo 범위를 벗어났습니다")) from exc


def _is_windows_uri(uri_path: str, uri_netloc: str, repo_root: str) -> bool:
    """Windows 스타일 URI/경로 여부를 판별한다."""
    if uri_netloc.strip() != "":
        return True
    if len(uri_path) >= 4 and uri_path[0] == "/" and uri_path[2] == ":":
        return True
    if len(uri_path) >= 3 and uri_path[1] == ":":
        return True
    if ":\\" in repo_root or ":/" in repo_root:
        return True
    return False


def _file_uri_to_repo_relative_windows(parsed, decoded_path: str, repo_root: str) -> str:  # type: ignore[no-untyped-def]
    """Windows file URI를 repo 상대 경로로 변환한다."""
    normalized = decoded_path.replace("/", "\\")
    # file:///C:/path -> C:\path
    if len(normalized) >= 4 and normalized[0] == "\\" and normalized[2] == ":":
        normalized = normalized[1:]
    # file://server/share/path -> \\server\share\path
    if parsed.netloc.strip() != "":
        normalized = f"\\\\{parsed.netloc}{normalized}"

    absolute_path = PureWindowsPath(normalized)
    repo_path = PureWindowsPath(repo_root)
    absolute_parts = tuple(part.lower() for part in absolute_path.parts)
    repo_parts = tuple(part.lower() for part in repo_path.parts)
    if len(repo_parts) == 0 or absolute_parts[: len(repo_parts)] != repo_parts:
        raise ValidationError(ErrorContext(code="ERR_URI_PATH_INVALID", message="uri가 repo 범위를 벗어났습니다"))
    relative_parts = absolute_path.parts[len(repo_path.parts) :]
    return PureWindowsPath(*relative_parts).as_posix()
