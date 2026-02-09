import json
import pytest
import io
import os
import socket
import threading
import time
from unittest.mock import MagicMock, patch
from sari.mcp.proxy import main as proxy_main, _read_mcp_message

def test_proxy_message_reading_variations():
    """다양한 프레이밍 모드에서의 메시지 읽기 안정성 검증"""
    # 1. JSONL mode
    stdin = io.BytesIO(b'{"jsonrpc": "2.0", "method": "ping"}\n')
    msg, mode = _read_mcp_message(stdin)
    assert mode == "jsonl"
    assert b"ping" in msg

    # 2. Content-Length mode (standard)
    body = b'{"jsonrpc": "2.0", "id": 1}'
    header = f"Content-Length: {len(body)}\r\n\r\n".encode('ascii')
    stdin = io.BytesIO(header + body)
    msg, mode = _read_mcp_message(stdin)
    assert mode == "framed"
    assert len(msg) == len(body)

@pytest.mark.timeout(5)
def test_proxy_handles_daemon_unavailable():
    """데몬이 응답하지 않을 때 프록시의 에러 처리 및 종료 로직 검증"""
    # Mocking socket to fail connection
    with patch("socket.create_connection", side_effect=ConnectionRefusedError()):
        with patch("sys.stdin", io.BytesIO(b'{"jsonrpc": "2.0", "id": 1, "method": "list_tools"}\n')):
            # Redirect stdout to capture error response (including buffer for binary mode)
            mock_stdout = MagicMock()
            mock_buffer = io.BytesIO()
            mock_stdout.buffer = mock_buffer
            
            with patch("sys.stdout", mock_stdout):
                try:
                    proxy_main()
                except SystemExit:
                    pass
                
                # 프록시는 데몬 연결 실패 시 클라이언트에게 에러 JSON-RPC를 보내야 함
                output = mock_buffer.getvalue().decode('utf-8')
                assert "error" in output.lower()
                assert "-32002" in output # Connection failed error code

def test_proxy_interleaved_noise_filtering():
    """통신 스트림 중간에 끼어든 노이즈가 프로토콜을 파괴하지 않는지 검증"""
    raw_input = (
        b"Welcome message\n"
        b'{"jsonrpc": "2.0", "method": "initialize"}\n'
        b"Some log from library\n"
    )
    stdin = io.BytesIO(raw_input)
    
    # First message should be found despite welcome message
    msg, mode = _read_mcp_message(stdin)
    assert mode == "jsonl"
    assert b"initialize" in msg