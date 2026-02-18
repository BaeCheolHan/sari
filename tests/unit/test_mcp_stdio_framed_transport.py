"""MCP stdio 프레이밍(Content-Length) 전송을 검증한다."""

from __future__ import annotations

import io
import json
from pathlib import Path

from pytest import MonkeyPatch
from sari.mcp.transport import McpTransport
from sari.mcp.server import run_stdio_streams


def _make_frame(payload: dict[str, object]) -> bytes:
    """JSON payload를 Content-Length 프레임 바이트로 변환한다."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _read_first_frame(output: bytes) -> dict[str, object]:
    """출력 바이트에서 첫 번째 framed 응답을 파싱한다."""
    header_end = output.find(b"\r\n\r\n")
    assert header_end > 0
    header = output[:header_end].decode("ascii")
    prefix = "Content-Length: "
    assert header.startswith(prefix)
    length = int(header[len(prefix):].strip())
    body = output[header_end + 4:header_end + 4 + length]
    return json.loads(body.decode("utf-8"))


def test_run_stdio_streams_reads_framed_and_writes_framed(tmp_path: Path) -> None:
    """framed 입력 요청을 받아 framed 응답을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    input_stream = io.BytesIO(_make_frame(request))
    output_stream = io.BytesIO()

    exit_code = run_stdio_streams(db_path=db_path, input_stream=input_stream, output_stream=output_stream)

    assert exit_code == 0
    raw_output = output_stream.getvalue()
    assert raw_output.startswith(b"Content-Length: ")
    payload = _read_first_frame(raw_output)
    assert payload["result"]["serverInfo"]["name"] == "sari-v2"


def test_run_stdio_streams_reads_jsonl_and_writes_jsonl(tmp_path: Path) -> None:
    """jsonl 입력 요청을 받아 jsonl 응답을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    request = {"jsonrpc": "2.0", "id": 2, "method": "initialize"}
    input_stream = io.BytesIO((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
    output_stream = io.BytesIO()

    exit_code = run_stdio_streams(db_path=db_path, input_stream=input_stream, output_stream=output_stream)

    assert exit_code == 0
    raw_output = output_stream.getvalue()
    assert raw_output.startswith(b"{")
    payload = json.loads(raw_output.decode("utf-8").strip())
    assert payload["result"]["serverInfo"]["name"] == "sari-v2"


def test_run_stdio_streams_returns_parse_error_for_invalid_utf8_jsonl(tmp_path: Path) -> None:
    """JSONL UTF-8 디코드 실패를 명시적 parse error로 반환해야 한다."""
    db_path = tmp_path / "state.db"
    input_stream = io.BytesIO(b"\xff\xfe\xfd\n")
    output_stream = io.BytesIO()

    exit_code = run_stdio_streams(db_path=db_path, input_stream=input_stream, output_stream=output_stream)

    assert exit_code == 0
    raw_output = output_stream.getvalue()
    assert raw_output.startswith(b"Content-Length: ")
    payload = _read_first_frame(raw_output)
    assert payload["error"]["code"] == -32700
    assert "utf-8" in payload["error"]["message"].lower()


def test_transport_write_message_sanitizes_lone_surrogate() -> None:
    """고립 surrogate 문자열이 있어도 write_message는 실패하지 않아야 한다."""
    input_stream = io.BytesIO()
    output_stream = io.BytesIO()
    transport = McpTransport(input_stream=input_stream, output_stream=output_stream)

    transport.write_message({"jsonrpc": "2.0", "id": 1, "result": {"text": "\ud800"}})

    raw = output_stream.getvalue()
    payload = _read_first_frame(raw)
    assert payload["result"]["text"] == "\ufffd"


def test_run_stdio_streams_calls_server_close_on_eof(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """EOF 종료 경로에서도 MCP 서버 close가 호출되어야 한다."""

    close_called = {"value": False}

    class _RuntimeRepo:
        def get_runtime(self) -> None:
            return None

    class _FakeServer:
        def __init__(self, db_path: Path) -> None:
            del db_path
            self._runtime_repo = _RuntimeRepo()

        def handle_request(self, payload: dict[str, object]) -> object:
            del payload
            raise AssertionError("EOF 경로에서는 handle_request가 호출되면 안 됩니다")

        def close(self) -> None:
            close_called["value"] = True

    monkeypatch.setattr("sari.mcp.server.McpServer", _FakeServer)
    exit_code = run_stdio_streams(db_path=tmp_path / "state.db", input_stream=io.BytesIO(b""), output_stream=io.BytesIO())

    assert exit_code == 0
    assert close_called["value"] is True
