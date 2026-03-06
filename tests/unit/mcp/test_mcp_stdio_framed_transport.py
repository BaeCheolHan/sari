"""MCP stdio 프레이밍(Content-Length) 전송을 검증한다."""

from __future__ import annotations

import io
import json
from pathlib import Path

from pytest import MonkeyPatch
from sari.core.exceptions import ErrorContext, ValidationError
from sari.db.schema import connect, init_schema
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


def test_run_stdio_streams_returns_internal_error_when_handler_raises(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """handle_request 내부 예외는 transport 종료 대신 JSON-RPC internal error로 반환해야 한다."""

    class _RuntimeRepo:
        def get_runtime(self) -> None:
            return None

    class _FakeServer:
        def __init__(self, db_path: Path) -> None:
            del db_path
            self._runtime_repo = _RuntimeRepo()

        def handle_request(self, payload: dict[str, object]) -> object:
            del payload
            raise RuntimeError("boom")

        def close(self) -> None:
            return None

    monkeypatch.setattr("sari.mcp.server.McpServer", _FakeServer)
    request = {"jsonrpc": "2.0", "id": 77, "method": "initialize"}
    input_stream = io.BytesIO(_make_frame(request))
    output_stream = io.BytesIO()

    exit_code = run_stdio_streams(db_path=tmp_path / "state.db", input_stream=input_stream, output_stream=output_stream)

    assert exit_code == 0
    payload = _read_first_frame(output_stream.getvalue())
    assert payload["id"] == 77
    assert payload["error"]["code"] == -32603
    assert "RuntimeError" in payload["error"]["message"]


def test_run_stdio_streams_degrades_on_repo_id_integrity_failure(tmp_path: Path) -> None:
    """repo_id integrity 오류가 있어도 stdio handshake는 framed 응답으로 유지해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO candidate_index_changes(
                change_type, status, repo_id, repo_root, scope_repo_root, relative_path,
                absolute_path, content_hash, mtime_ns, size_bytes, event_source, reason, created_at, updated_at
            ) VALUES(
                'UPSERT', 'PENDING', '', '/broken/repo', '/broken/repo', 'a.py',
                '/broken/repo/a.py', 'h1', 1, 10, 'test', NULL, '2026-03-05T00:00:00Z', '2026-03-05T00:00:00Z'
            )
            """
        )
        conn.commit()

    requests = b"".join(
        [
            _make_frame({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            _make_frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            _make_frame(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "search", "arguments": {"repo": "sari", "query": "x", "structured": 1}},
                    }
                ),
            ]
        )
    input_stream = io.BytesIO(requests)
    output_stream = io.BytesIO()

    exit_code = run_stdio_streams(db_path=db_path, input_stream=input_stream, output_stream=output_stream)

    assert exit_code == 0
    raw_output = output_stream.getvalue()
    assert raw_output.startswith(b"Content-Length: ")
    frames: list[dict[str, object]] = []
    remaining = raw_output
    while remaining:
        header_end = remaining.find(b"\r\n\r\n")
        assert header_end > 0
        header = remaining[:header_end].decode("ascii")
        prefix = "Content-Length: "
        assert header.startswith(prefix)
        length = int(header[len(prefix):].strip())
        body_start = header_end + 4
        body = remaining[body_start:body_start + length]
        frames.append(json.loads(body.decode("utf-8")))
        remaining = remaining[body_start + length:]

    assert frames[0]["result"]["serverInfo"]["name"] == "sari-v2"
    tools = frames[1]["result"]["tools"]
    tool_names = {str(tool["name"]) for tool in tools}
    assert tool_names == {"doctor", "repo_candidates", "sari_guide", "status"}
    tool_call_payload = frames[2]
    assert "error" not in tool_call_payload
    assert tool_call_payload["result"]["isError"] is True
    structured = tool_call_payload["result"]["structuredContent"]
    assert structured["error"]["code"] == "ERR_MCP_STARTUP_DEGRADED"
    assert structured["error"]["recovery_hint"] == "Run status or doctor to inspect startup degradation before retrying."


def test_run_stdio_streams_fatal_startup_error_does_not_write_stdout(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys,
) -> None:
    """fatal startup 오류는 stdout 프레임을 쓰지 않고 stderr + exit code로만 끝나야 한다."""

    def _raise_bootstrap(db_path: Path):
        del db_path
        raise ValidationError(ErrorContext(code="ERR_BOOTSTRAP_FATAL", message="fatal bootstrap"))

    monkeypatch.setattr("sari.mcp.server._build_stdio_server", _raise_bootstrap)
    input_stream = io.BytesIO(_make_frame({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
    output_stream = io.BytesIO()

    exit_code = run_stdio_streams(db_path=tmp_path / "state.db", input_stream=input_stream, output_stream=output_stream)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert output_stream.getvalue() == b""
    assert "ERR_BOOTSTRAP_FATAL" in captured.err
