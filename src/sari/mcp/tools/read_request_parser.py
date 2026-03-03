"""read 요청 파싱/검증을 담당한다."""

from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import ErrorResponseDTO
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.tools.tool_common import argument_error


@dataclass(frozen=True)
class ParsedReadRequest:
    """read 공통 파싱 결과 DTO."""

    repo_root: str
    mode: str


class ReadRequestParser:
    """read 공통 입력 파싱기."""

    def parse(self, arguments: dict[str, object], repo_root: str) -> tuple[ParsedReadRequest | None, dict[str, object] | None]:
        """mode를 파싱하고 공통 검증 오류를 반환한다."""
        mode_raw = arguments.get("mode", "file")
        if not isinstance(mode_raw, str) or mode_raw.strip() == "":
            return (
                None,
                argument_error(
                    code="ERR_MODE_REQUIRED",
                    message="mode is required",
                    arguments=arguments,
                    expected=["mode"],
                    example={"repo": repo_root, "mode": "file", "target": "README.md"},
                ),
            )
        mode = mode_raw.strip().lower()
        if mode == "ast_edit":
            return None, pack1_error(ErrorResponseDTO(code="ERR_AST_DISABLED", message="ast_edit mode is disabled by policy"))
        if mode not in {"file", "symbol", "snippet", "diff_preview"}:
            return (
                None,
                argument_error(
                    code="ERR_UNSUPPORTED_MODE",
                    message=f"unsupported mode: {mode}",
                    arguments=arguments,
                    expected=["file", "symbol", "snippet", "diff_preview"],
                    example={"repo": repo_root, "mode": "file", "target": "README.md"},
                ),
            )
        return ParsedReadRequest(repo_root=repo_root, mode=mode), None

