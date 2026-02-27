"""HTTP 응답 빌더 유틸을 검증한다."""

from __future__ import annotations

from sari.http.response_builders import extract_read_error, pack1_to_http_json
from sari.mcp.tools.pack1 import pack1_error
from sari.core.models import ErrorResponseDTO


def test_extract_read_error_reads_recovery_hint_from_structured_error() -> None:
    """pack1 structuredContent.error의 recovery_hint를 우선 추출해야 한다."""
    payload = pack1_error(
        ErrorResponseDTO(code="ERR_LSP_UNAVAILABLE", message="lsp unavailable"),
        recovery_hint="install missing language servers",
    )
    code, message, recovery_hint = extract_read_error(payload)
    assert code == "ERR_LSP_UNAVAILABLE"
    assert message == "lsp unavailable"
    assert recovery_hint == "install missing language servers"


def test_pack1_to_http_json_includes_recovery_hint_on_error() -> None:
    """HTTP JSON 변환 시 recovery_hint를 error payload에 유지해야 한다."""
    payload = pack1_error(
        ErrorResponseDTO(code="ERR_LSP_UNAVAILABLE", message="lsp unavailable"),
        recovery_hint="run pipeline lsp-matrix diagnose",
    )
    body, status = pack1_to_http_json(payload)
    assert status == 400
    assert body["error"]["code"] == "ERR_LSP_UNAVAILABLE"
    assert body["error"]["recovery_hint"] == "run pipeline lsp-matrix diagnose"

