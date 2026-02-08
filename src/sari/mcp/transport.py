import json
import sys
import logging
from typing import Any, Dict, Optional, Tuple, BinaryIO
from sari.mcp.trace import trace

logger = logging.getLogger("sari.mcp.transport")

_MODE_FRAMED = "content-length"
_MODE_JSONL = "jsonl"
MAX_MESSAGE_SIZE = 10 * 1024 * 1024 # 10MB

class McpTransport:
    """
    Handles the low-level MCP wire protocol (Framing).
    Encapsulates Content-Length and JSONL logic.
    """
    
    def __init__(self, input_stream: BinaryIO, output_stream: BinaryIO, allow_jsonl: bool = True):
        self.input = input_stream
        self.output = output_stream
        self.allow_jsonl = allow_jsonl
        self.default_mode = _MODE_FRAMED

    def read_message(self) -> Optional[Tuple[Dict[str, Any], str]]:
        """
        Reads one MCP message from the input stream.
        Returns: (parsed_dict, framing_mode) or None if EOF/Error.
        """
        try:
            line = self.input.readline()
            if not line:
                trace("transport_read_eof")
                return None
            
            while line in (b"\n", b"\r\n"):
                line = self.input.readline()
                if not line:
                    return None

            line_str = line.decode("utf-8", errors="ignore").strip()
            if not line_str:
                return None

            if line_str.startswith("{"):
                # JSONL Mode
                if not self.allow_jsonl:
                    logger.debug("JSONL detected but not allowed. Ignoring.")
                    trace("transport_jsonl_not_allowed")
                    return None
                trace("transport_read_jsonl")
                return self._parse_json(line_str), _MODE_JSONL

            if line_str.lower().startswith("content-length:"):
                # Content-Length Mode
                content_length = self._parse_headers(line_str)
                if content_length is None or content_length <= 0 or content_length > MAX_MESSAGE_SIZE:
                    trace("transport_invalid_content_length", value=content_length)
                    return None

                body_bytes = b""
                while len(body_bytes) < content_length:
                    chunk = self.input.read(content_length - len(body_bytes))
                    if not chunk:
                        break
                    body_bytes += chunk
                
                if len(body_bytes) < content_length:
                    trace("transport_incomplete_body", expected=content_length, actual=len(body_bytes))
                    return None
                trace("transport_read_framed", bytes=content_length)
                return self._parse_json(body_bytes.decode("utf-8")), _MODE_FRAMED

            return None
        except Exception as e:
            logger.error(f"Error reading MCP message: {e}")
            trace("transport_read_error", error=str(e))
            return None

    def write_message(self, message: Dict[str, Any], mode: Optional[str] = None):
        """
        Writes one MCP message to the output stream.
        """
        try:
            json_str = json.dumps(message, ensure_ascii=False)
            mode = mode or self.default_mode
            
            if mode == _MODE_JSONL:
                payload = (json_str + "\n").encode("utf-8")
                self.output.write(payload)
                trace("transport_write_jsonl", bytes=len(payload))
            else:
                body_bytes = json_str.encode("utf-8")
                header = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("ascii")
                self.output.write(header + body_bytes)
                trace("transport_write_framed", bytes=len(body_bytes))
            
            self.output.flush()
        except Exception as e:
            logger.error(f"Error writing MCP message: {e}")
            trace("transport_write_error", error=str(e))

    def _parse_headers(self, first_line: str) -> Optional[int]:
        headers = {}
        parts = first_line.split(":", 1)
        headers[parts[0].strip().lower()] = parts[1].strip()
        
        # Read remaining headers until separator
        while True:
            line = self.input.readline()
            if not line:
                break
            h_str = line.decode("utf-8").strip()
            if not h_str:
                break # Separator
            if ":" in h_str:
                k, v = h_str.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        
        try:
            return int(headers.get("content-length", 0))
        except (ValueError, TypeError):
            return None

    def _parse_json(self, data: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(data)
        except Exception:
            return None

    def close(self) -> None:
        """Best-effort close for non-stdio streams."""
        stdio_streams = {getattr(sys.stdin, "buffer", None), getattr(sys.stdout, "buffer", None), getattr(sys.stderr, "buffer", None)}
        for stream in (self.input, self.output):
            if stream in stdio_streams:
                continue
            try:
                stream.close()
            except Exception:
                pass


class AsyncMcpTransport:
    """
    비동기 MCP Transport 레이어.
    
    Session에서 사용하기 위한 asyncio 기반 구현.
    Content-Length와 JSONL 모드 모두 지원.
    """
    
    def __init__(self, reader, writer, allow_jsonl: bool = True):
        self.reader = reader
        self.writer = writer
        self.allow_jsonl = allow_jsonl
        self.default_mode = _MODE_FRAMED
    
    async def read_message(self) -> Optional[Tuple[Dict[str, Any], str]]:
        """
        비동기적으로 한 개의 MCP 메시지를 읽습니다.
        Returns: (parsed_dict, framing_mode) 또는 EOF/Error 시 None
        """
        try:
            line = await self.reader.readline()
            if not line:
                trace("transport_async_read_eof")
                return None
            
            # 빈 줄 스킵
            while line in (b"\n", b"\r\n"):
                line = await self.reader.readline()
                if not line:
                    return None
            
            line_str = line.decode("utf-8", errors="ignore").strip()
            if not line_str:
                return None
            
            # JSONL 모드 감지
            if line_str.startswith("{"):
                if not self.allow_jsonl:
                    logger.debug("JSONL detected but not allowed.")
                    trace("transport_async_jsonl_not_allowed")
                    return None
                trace("transport_async_read_jsonl")
                return self._parse_json(line_str), _MODE_JSONL
            
            # Content-Length 모드 감지
            if line_str.lower().startswith("content-length:"):
                content_length = await self._parse_headers_async(line_str)
                if content_length is None or content_length <= 0 or content_length > MAX_MESSAGE_SIZE:
                    trace("transport_async_invalid_content_length", value=content_length)
                    return None
                
                body = await self.reader.readexactly(content_length)
                trace("transport_async_read_framed", bytes=content_length)
                return self._parse_json(body.decode("utf-8")), _MODE_FRAMED
            
            return None
        except Exception as e:
            logger.error(f"Error reading async MCP message: {e}")
            trace("transport_async_read_error", error=str(e))
            return None
    
    async def write_message(self, message: Dict[str, Any], mode: Optional[str] = None) -> None:
        """비동기적으로 MCP 메시지를 전송합니다."""
        try:
            json_str = json.dumps(message, ensure_ascii=False)
            mode = mode or self.default_mode
            
            if mode == _MODE_JSONL:
                payload = (json_str + "\n").encode("utf-8")
                self.writer.write(payload)
                trace("transport_async_write_jsonl", bytes=len(payload))
            else:
                body_bytes = json_str.encode("utf-8")
                header = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("ascii")
                self.writer.write(header + body_bytes)
                trace("transport_async_write_framed", bytes=len(body_bytes))
            
            await self.writer.drain()
        except Exception as e:
            logger.error(f"Error writing async MCP message: {e}")
            trace("transport_async_write_error", error=str(e))
    
    async def _parse_headers_async(self, first_line: str) -> Optional[int]:
        """비동기적으로 HTTP 스타일 헤더를 파싱합니다."""
        headers = {}
        parts = first_line.split(":", 1)
        headers[parts[0].strip().lower()] = parts[1].strip()
        
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
        
        try:
            return int(headers.get("content-length", 0))
        except (ValueError, TypeError):
            return None
    
    def _parse_json(self, data: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(data)
        except Exception:
            return None
