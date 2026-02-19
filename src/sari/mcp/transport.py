"""MCP stdio 전송 계층(Content-Length/JSONL)을 제공한다."""

from __future__ import annotations

import json
import time
from typing import BinaryIO

MCP_MODE_FRAMED = "content-length"
MCP_MODE_JSONL = "jsonl"
MAX_MESSAGE_SIZE = 10 * 1024 * 1024
MAX_DRAIN_BYTES = 2 * 1024 * 1024
MAX_DRAIN_TIME_MS = 300


class McpTransportParseError(Exception):
    """전송 계층에서 메시지 파싱에 실패했음을 나타낸다."""

    def __init__(self, mode: str, message: str, *, reason_code: str, recoverable: bool) -> None:
        """오류 모드/이유/복구 가능 여부를 저장한다."""
        super().__init__(message)
        self.mode = mode
        self.reason_code = reason_code
        self.recoverable = recoverable


class McpTransport:
    """MCP wire protocol 읽기/쓰기를 담당한다."""

    def __init__(self, input_stream: BinaryIO, output_stream: BinaryIO, allow_jsonl: bool = True) -> None:
        """입출력 스트림과 모드를 초기화한다."""
        self._input = input_stream
        self._output = output_stream
        self._allow_jsonl = allow_jsonl
        self.default_mode = MCP_MODE_FRAMED
        self._read_buffer = bytearray()

    def read_message(self) -> tuple[dict[str, object], str] | None:
        """다음 MCP 메시지를 읽어 payload와 모드를 반환한다."""
        while True:
            line = self._readline_from_stream()
            if line == b"":
                return None

            line_text = self._decode_utf8_strict(line=line, mode=MCP_MODE_FRAMED).strip()
            if line_text == "":
                continue

            if line_text.startswith("{"):
                if not self._allow_jsonl:
                    raise McpTransportParseError(
                        mode=MCP_MODE_JSONL,
                        message="jsonl payload is disabled",
                        reason_code="jsonl_disabled",
                        recoverable=True,
                    )
                payload = self._parse_json_object(line_text)
                if payload is not None:
                    return payload, MCP_MODE_JSONL
                raise McpTransportParseError(
                    mode=MCP_MODE_JSONL,
                    message="parse error",
                    reason_code="invalid_json",
                    recoverable=True,
                )

            if line_text.lower().startswith("content-length:"):
                content_length = self._parse_headers(first_line=line_text)
                if content_length <= 0:
                    raise McpTransportParseError(
                        mode=MCP_MODE_FRAMED,
                        message=f"invalid Content-Length: {content_length}",
                        reason_code="invalid_content_length",
                        recoverable=True,
                    )
                if content_length > MAX_MESSAGE_SIZE:
                    raise McpTransportParseError(
                        mode=MCP_MODE_FRAMED,
                        message=f"Content-Length too large: {content_length}",
                        reason_code="content_length_too_large",
                        recoverable=False,
                    )

                body = self._read_exact(content_length)
                if len(body) < content_length:
                    raise McpTransportParseError(
                        mode=MCP_MODE_FRAMED,
                        message="truncated framed body",
                        reason_code="body_truncated",
                        recoverable=False,
                    )
                body_text = self._decode_utf8_strict(line=body, mode=MCP_MODE_FRAMED)
                payload = self._parse_json_object(body_text)
                if payload is not None:
                    return payload, MCP_MODE_FRAMED
                raise McpTransportParseError(
                    mode=MCP_MODE_FRAMED,
                    message="parse error",
                    reason_code="invalid_json",
                    recoverable=True,
                )

            raise McpTransportParseError(
                mode=MCP_MODE_FRAMED,
                message=f"invalid frame header: {line_text[:64]}",
                reason_code="invalid_header",
                recoverable=True,
            )

    def write_message(self, message: dict[str, object], mode: str | None = None) -> None:
        """모드에 맞춰 MCP 응답 메시지를 출력한다."""
        safe_message = _sanitize_json_value(message)
        encoded = json.dumps(safe_message, ensure_ascii=False).encode("utf-8")
        selected_mode = mode if mode is not None else self.default_mode
        if selected_mode == MCP_MODE_JSONL:
            self._output.write(encoded + b"\n")
            self._output.flush()
            return

        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
        self._output.write(header + encoded)
        self._output.flush()

    def _parse_headers(self, first_line: str) -> int:
        """헤더 블록에서 content-length를 파싱한다."""
        headers: dict[str, str] = {}
        first_key, first_value = self._split_header(first_line)
        if first_key is None or first_value is None:
            raise McpTransportParseError(
                mode=MCP_MODE_FRAMED,
                message=f"invalid header line: {first_line}",
                reason_code="invalid_header",
                recoverable=True,
            )
        headers[first_key] = first_value

        while True:
            line = self._readline_from_stream()
            if line == b"":
                break
            header_line = self._decode_utf8_strict(line=line, mode=MCP_MODE_FRAMED).strip()
            if header_line == "":
                break
            key, value = self._split_header(header_line)
            if key is None or value is None:
                raise McpTransportParseError(
                    mode=MCP_MODE_FRAMED,
                    message=f"invalid header line: {header_line}",
                    reason_code="invalid_header",
                    recoverable=True,
                )
            headers[key] = value

        raw_length = headers.get("content-length")
        if raw_length is None:
            raise McpTransportParseError(
                mode=MCP_MODE_FRAMED,
                message="missing Content-Length header",
                reason_code="missing_content_length",
                recoverable=True,
            )
        try:
            return int(raw_length)
        except ValueError:
            raise McpTransportParseError(
                mode=MCP_MODE_FRAMED,
                message=f"invalid Content-Length value: {raw_length}",
                reason_code="invalid_content_length",
                recoverable=True,
            )

    @staticmethod
    def _split_header(line: str) -> tuple[str | None, str | None]:
        """단일 헤더 라인을 key/value로 분리한다."""
        if ":" not in line:
            return None, None
        key, value = line.split(":", 1)
        return key.strip().lower(), value.strip()

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, object] | None:
        """JSON 문자열을 dict payload로 파싱한다."""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        normalized: dict[str, object] = {}
        for key, value in parsed.items():
            if isinstance(key, str):
                normalized[key] = value
        return normalized

    @staticmethod
    def _decode_utf8_strict(line: bytes, mode: str) -> str:
        """UTF-8 디코드 실패를 침묵하지 않고 전송 계층 오류로 변환한다."""
        try:
            return line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise McpTransportParseError(
                mode=mode,
                message=f"invalid utf-8 payload: {exc}",
                reason_code="invalid_utf8",
                recoverable=True,
            ) from exc

    def drain_for_resync(self, mode: str) -> bool:
        """손상된 입력 이후 다음 프레임 경계까지 드레인해 재동기화를 시도한다."""
        start = time.monotonic()
        drained = 0
        if mode == MCP_MODE_JSONL:
            while drained < MAX_DRAIN_BYTES and (time.monotonic() - start) * 1000 <= MAX_DRAIN_TIME_MS:
                chunk = self._input.read(1)
                if chunk == b"":
                    return False
                drained += 1
                if chunk == b"\n":
                    return True
            return False

        buffer = bytearray()
        marker = b"Content-Length:"
        while drained < MAX_DRAIN_BYTES and (time.monotonic() - start) * 1000 <= MAX_DRAIN_TIME_MS:
            chunk = self._input.read(1)
            if chunk == b"":
                return False
            drained += 1
            buffer.extend(chunk)
            if len(buffer) > len(marker) * 4:
                del buffer[: len(buffer) - len(marker) * 4]
            marker_index = buffer.rfind(marker)
            if marker_index >= 0:
                self._read_buffer.extend(buffer[marker_index:])
                return True
        return False

    def _readline_from_stream(self) -> bytes:
        """내부 버퍼를 우선 소비해 한 줄을 읽는다."""
        line = bytearray()
        while True:
            if len(self._read_buffer) > 0:
                byte = bytes([self._read_buffer.pop(0)])
            else:
                byte = self._input.read(1)
            if byte == b"":
                return bytes(line)
            line.extend(byte)
            if byte == b"\n":
                return bytes(line)

    def _read_exact(self, size: int) -> bytes:
        """내부 버퍼를 우선 소비해 지정 길이만큼 읽는다."""
        chunks = bytearray()
        while len(chunks) < size:
            if len(self._read_buffer) > 0:
                remaining = size - len(chunks)
                take = min(remaining, len(self._read_buffer))
                chunks.extend(self._read_buffer[:take])
                del self._read_buffer[:take]
                continue
            chunk = self._input.read(size - len(chunks))
            if chunk == b"":
                break
            chunks.extend(chunk)
        return bytes(chunks)


def _sanitize_json_value(value: object) -> object:
    """JSON 직렬화 경계에서 비정상 텍스트를 안전 문자로 치환한다."""
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, child in value.items():
            if isinstance(key, str):
                sanitized[_sanitize_text(key)] = _sanitize_json_value(child)
        return sanitized
    return value


def _sanitize_text(text: str) -> str:
    """고립 surrogate 문자를 U+FFFD로 치환해 UTF-8 인코딩 실패를 방지한다."""
    if text == "":
        return text
    chars: list[str] = []
    for ch in text:
        code = ord(ch)
        if 55296 <= code <= 57343:
            chars.append("\ufffd")
            continue
        chars.append(ch)
    return "".join(chars)
