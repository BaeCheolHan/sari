import json
import sys
import logging
from typing import Optional, TypeAlias, BinaryIO
from sari.mcp.trace import trace

logger = logging.getLogger("sari.mcp.transport")

_MODE_FRAMED = "content-length"
_MODE_JSONL = "jsonl"
MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB
JsonObject: TypeAlias = dict[str, object]


class McpTransport:
    """
    Handles the low-level MCP wire protocol (Framing) with high fault tolerance.
    """

    def __init__(
            self,
            input_stream: BinaryIO,
            output_stream: BinaryIO,
            allow_jsonl: bool = True):
        self.input = input_stream
        self.output = output_stream
        self.allow_jsonl = allow_jsonl
        self.default_mode = _MODE_FRAMED

    def read_message(self) -> Optional[tuple[JsonObject, str]]:
        """
        Robustly reads one MCP message, skipping leading noise or empty lines.
        """
        try:
            while True:
                line = self.input.readline()
                if not line:
                    return None  # EOF

                line_str = line.decode("utf-8", errors="ignore").strip()
                if not line_str:
                    continue  # Skip empty lines

                # 1. Check for JSONL Mode (Starts with '{')
                if line_str.startswith("{"):
                    if self.allow_jsonl:
                        msg = self._parse_json(line_str)
                        if msg:
                            trace("transport_read_jsonl")
                            return msg, _MODE_JSONL
                    continue  # Not a valid JSON, keep looking

                # 2. Check for Content-Length Mode
                if line_str.lower().startswith("content-length:"):
                    content_length = self._parse_headers(line_str)
                    if content_length is None or content_length <= 0 or content_length > MAX_MESSAGE_SIZE:
                        continue  # Invalid header, keep looking for next valid marker

                    try:
                        body_bytes = self.input.read(content_length)
                        if len(body_bytes) < content_length:
                            return None  # Unexpected EOF

                        msg = self._parse_json(body_bytes.decode("utf-8"))
                        if msg:
                            trace(
                                "transport_read_framed",
                                bytes=content_length)
                            return msg, _MODE_FRAMED
                    except Exception:
                        continue  # Malformed body, try again

                # 3. If we are here, it means the line was noise (logs, etc.)
                # Keep looping to find the actual start of an MCP message

        except Exception as e:
            logger.error(f"Error reading MCP message: {e}")
            return None

    def write_message(
            self, message: JsonObject, mode: Optional[str] = None):
        """Writes one MCP message with proper framing."""
        try:
            json_str = json.dumps(message, ensure_ascii=False)
            mode = mode or self.default_mode

            if mode == _MODE_JSONL:
                payload = (json_str + "\n").encode("utf-8")
                self.output.write(payload)
            else:
                body_bytes = json_str.encode("utf-8")
                header = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode(
                    "ascii")
                self.output.write(header + body_bytes)

            self.output.flush()
        except Exception as e:
            logger.error(f"Error writing MCP message: {e}")

    def _parse_headers(self, first_line: str) -> Optional[int]:
        try:
            headers = {}
            # Parse the first line already read
            k, v = first_line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

            # Read subsequent headers until an empty line
            while True:
                line = self.input.readline()
                if not line:
                    break
                h_str = line.decode("utf-8").strip()
                if not h_str:
                    break  # Header end marker (\r\n\r\n)
                if ":" in h_str:
                    k, v = h_str.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            return int(headers.get("content-length", 0))
        except (ValueError, TypeError, Exception):
            return None

    def _parse_json(self, data: str) -> Optional[JsonObject]:
        try:
            payload = json.loads(data)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def close(self) -> None:
        stdio_streams = {
            getattr(
                sys.stdin, "buffer", None), getattr(
                sys.stdout, "buffer", None)}
        for stream in (self.input, self.output):
            if stream and stream not in stdio_streams:
                try:
                    stream.close()
                except Exception:
                    pass


class AsyncMcpTransport:
    """
    Async version of McpTransport with noise resilience.
    """

    def __init__(self, reader, writer, allow_jsonl: bool = True):
        self.reader = reader
        self.writer = writer
        self.allow_jsonl = allow_jsonl
        self.default_mode = _MODE_FRAMED

    async def read_message(self) -> Optional[tuple[JsonObject, str]]:
        """
        Asynchronously reads one MCP message, skipping noise.
        """
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    return None

                line_str = line.decode("utf-8", errors="ignore").strip()
                if not line_str:
                    continue

                if line_str.startswith("{"):
                    if self.allow_jsonl:
                        msg = self._parse_json(line_str)
                        if msg:
                            return msg, _MODE_JSONL
                    continue

                if line_str.lower().startswith("content-length:"):
                    content_length = await self._parse_headers_async(line_str)
                    if content_length is None or content_length <= 0 or content_length > MAX_MESSAGE_SIZE:
                        continue

                    try:
                        body = await self.reader.readexactly(content_length)
                        msg = self._parse_json(body.decode("utf-8"))
                        if msg:
                            return msg, _MODE_FRAMED
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"Async transport read error: {e}")
            return None

    async def write_message(
            self, message: JsonObject, mode: Optional[str] = None) -> None:
        try:
            json_str = json.dumps(message, ensure_ascii=False)
            mode = mode or self.default_mode

            if mode == _MODE_JSONL:
                self.writer.write((json_str + "\n").encode("utf-8"))
            else:
                body_bytes = json_str.encode("utf-8")
                header = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode(
                    "ascii")
                self.writer.write(header + body_bytes)

            await self.writer.drain()
        except Exception as e:
            logger.error(f"Async transport write error: {e}")

    async def _parse_headers_async(self, first_line: str) -> Optional[int]:
        try:
            headers = {}
            k, v = first_line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

            while True:
                line = await self.reader.readline()
                if not line:
                    break
                h_str = line.decode("utf-8").strip()
                if not h_str:
                    break
                if ":" in h_str:
                    k, v = h_str.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            return int(headers.get("content-length", 0))
        except Exception:
            return None

    def _parse_json(self, data: str) -> Optional[JsonObject]:
        try:
            payload = json.loads(data)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None
