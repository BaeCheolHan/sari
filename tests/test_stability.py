import json
import pytest
import io
import os
import asyncio
import sys
from unittest.mock import MagicMock, patch
from sari.mcp.server import LocalSearchMCPServer
from sari.mcp.transport import McpTransport, AsyncMcpTransport
from sari.mcp.stdout_guard import StdoutGuard

class TestCommunicationResilience:
    
    # 1. MCP Transport Resilience (Noise Tolerance)
    def test_transport_skips_noise_and_finds_message(self):
        """메시지 앞에 로그나 노이즈가 있어도 실제 MCP 메시지를 찾아내는지 검증"""
        msg_dict = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        json_msg = json.dumps(msg_dict)
        
        # 노이즈 + Content-Length 헤더 + 메시지
        header = f"Content-Length: {len(json_msg)}\r\n\r\n".encode('ascii')
        raw_data = (
            b"DEBUG: some random log\n"
            b"Warning: system busy\n"
            b"\n"
            + header +
            json_msg.encode('utf-8')
        )
        
        transport = McpTransport(io.BytesIO(raw_data), io.BytesIO())
        result, mode = transport.read_message()
        
        assert result == msg_dict
        assert mode == "content-length"

    def test_transport_skips_noise_in_jsonl_mode(self):
        """JSONL 모드에서도 노이즈를 건너뛰고 유효한 JSON을 찾는지 검증"""
        msg_dict = {"jsonrpc": "2.0", "id": 2, "method": "list_tools"}
        
        raw_data = (
            b"Raw log line\n"
            + json.dumps(msg_dict).encode('utf-8') + b"\n"
        )
        
        transport = McpTransport(io.BytesIO(raw_data), io.BytesIO())
        result, mode = transport.read_message()
        
        assert result == msg_dict
        assert mode == "jsonl"

    @pytest.mark.asyncio
    async def test_async_transport_basic_flow(self):
        """비동기 트랜스포트의 읽기/쓰기 기본 동작 검증"""
        msg_dict = {"jsonrpc": "2.0", "method": "notify"}
        json_msg = json.dumps(msg_dict)
        
        # Mock Reader/Writer
        reader = asyncio.StreamReader()
        reader.feed_data(f"Content-Length: {len(json_msg)}\r\n\r\n{json_msg}".encode('utf-8'))
        reader.feed_eof()
        
        writer = MagicMock(spec=asyncio.StreamWriter)
        
        transport = AsyncMcpTransport(reader, writer)
        result, mode = await transport.read_message()
        
        assert result == msg_dict
        assert mode == "content-length"
        
        # Write test
        await transport.write_message({"id": 1, "result": "ok"})
        assert writer.write.called

    # 2. Stdout Guard Isolation
    def test_stdout_guard_isolates_noise(self):
        """StdoutGuard가 일반 print문은 stderr로 보내고, 프로토콜은 stdout으로 유지하는지 검증"""
        fake_stdout = io.StringIO()
        fake_stderr = io.StringIO()
        
        guard = StdoutGuard(fake_stdout, fallback=fake_stderr)
        
        # 1. 일반 출력 (노이즈)
        guard.write("This is a log message\n")
        assert fake_stdout.getvalue() == ""
        assert "This is a log message" in fake_stderr.getvalue()
        
        # 2. 공식 MCP 메시지 형태 (JSON-RPC)
        mcp_msg = '{"jsonrpc":"2.0", "id":1, "result":null}'
        guard.write(mcp_msg)
        assert mcp_msg in fake_stdout.getvalue()

    # 3. Server-level Stability
    def test_protocol_version_negotiation(self):
        server = LocalSearchMCPServer("/tmp")
        # 지원하는 버전 협상 성공 확인
        resp = server.handle_initialize({"protocolVersion": "2024-11-05"})
        assert resp["protocolVersion"] == "2024-11-05"
        
        # 지원하지 않는 버전은 서버 기본값으로 폴백 확인
        resp = server.handle_initialize({"protocolVersion": "9999-99-99"})
        assert resp["protocolVersion"] == server.PROTOCOL_VERSION