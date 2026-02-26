"""LSP extract 예외를 표준 에러 문자열로 매핑한다."""

from __future__ import annotations

from solidlsp.ls_exceptions import SolidLSPException

from sari.services.collection.l5.solid_lsp_probe_mixin import _is_workspace_mismatch_error


class LspExtractErrorMapper:
    """extract_once 예외 분류/메시지 포맷팅 책임을 분리한다."""

    def map_solid_exception(self, *, repo_root: str, normalized_relative_path: str, exc: SolidLSPException) -> str:
        message = str(exc)
        if _is_workspace_mismatch_error(message):
            return (
                f"ERR_LSP_WORKSPACE_MISMATCH: repo={repo_root}, path={normalized_relative_path}, reason={message}"
            )
        if "ERR_LSP_SYNC_OPEN_FAILED" in message:
            return f"ERR_LSP_SYNC_OPEN_FAILED: repo={repo_root}, path={normalized_relative_path}, reason={message}"
        if "ERR_LSP_SYNC_CHANGE_FAILED" in message:
            return f"ERR_LSP_SYNC_CHANGE_FAILED: repo={repo_root}, path={normalized_relative_path}, reason={message}"
        return f"ERR_LSP_DOCUMENT_SYMBOL_FAILED: repo={repo_root}, path={normalized_relative_path}, reason={message}"

    def map_generic_exception(self, exc: Exception) -> str:
        return f"LSP 추출 실패: {exc}"
