import pytest
import io
import asyncio
from unittest.mock import MagicMock
from sari.mcp.transport import McpTransport, AsyncMcpTransport, MAX_MESSAGE_SIZE

class TestCommunicationEdgeCases:
    
    def test_transport_invalid_content_length(self):
        """잘못된 Content-Length 헤더 처리 검증"""
        # 1. 음수 길이
        transport = McpTransport(io.BytesIO(b"Content-Length: -10\r\n\r\n{}"), io.BytesIO())
        assert transport.read_message() is None
        
        # 2. 너무 큰 길이 (DoS 방지)
        too_big = MAX_MESSAGE_SIZE + 1
        transport = McpTransport(io.BytesIO(f"Content-Length: {too_big}\r\n\r\n".encode('ascii')), io.BytesIO())
        assert transport.read_message() is None

    def test_transport_incomplete_message(self):
        """데이터가 읽히다가 끊기는 상황(EOF) 검증"""
        msg = b'{"jsonrpc": "2.0"}'
        # Content-Length는 100인데 실제 데이터는 훨씬 짧음
        transport = McpTransport(io.BytesIO(b"Content-Length: 100\r\n\r\n" + msg), io.BytesIO())
        assert transport.read_message() is None

    def test_transport_malformed_json(self):
        """JSON 문법 오류가 있는 메시지 스킵 및 다음 메시지 탐색 검증"""
        raw_data = (
            b'{"invalid": json}\n' # 문법 에러
            b'{"jsonrpc": "2.0", "id": 1, "result": "ok"}\n' # 정상
        )
        transport = McpTransport(io.BytesIO(raw_data), io.BytesIO())
        result, mode = transport.read_message()
        
        assert result["id"] == 1
        assert mode == "jsonl"

    @pytest.mark.asyncio
    async def test_async_transport_read_timeout_or_interruption(self):
        """비동기 환경에서 읽기 중단 시나리오 검증"""
        reader = asyncio.StreamReader()
        # 헤더만 보내고 바디는 보내지 않은 채 EOF
        reader.feed_data(b"Content-Length: 50\r\n\r\n")
        reader.feed_eof()
        
        transport = AsyncMcpTransport(reader, MagicMock())
        result = await transport.read_message()
        assert result is None

    def test_scanner_hard_exclude_rules(self):
        """인덱서 스캐너의 필수 제외 규칙 검증 (기존 테스트 유지)"""
        from sari.core.indexer.scanner import Scanner
        mock_cfg = MagicMock()
        mock_cfg.exclude_dirs = []
        scanner = Scanner(mock_cfg)
        assert ".git" in scanner.hard_exclude_dirs
        assert "node_modules" in scanner.hard_exclude_dirs