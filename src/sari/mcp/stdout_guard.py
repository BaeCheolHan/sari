"""
StdoutGuard: MCP 프로토콜 보호를 위한 stdout 래퍼

서드파티 라이브러리가 stdout에 직접 출력하면 MCP JSON-RPC 프로토콜이 오염됩니다.
이 모듈은 MCP 메시지만 실제 stdout으로 전달하고, 나머지는 stderr로 리다이렉트합니다.
"""
import sys
import threading
from typing import Any, TextIO


class StdoutGuard:
    """
    MCP 프로토콜 보호용 stdout 래퍼.
    
    - JSON-RPC 메시지 ({"jsonrpc"...)만 실제 stdout으로 전달
    - Content-Length 헤더도 허용
    - 나머지 모든 출력은 stderr로 리다이렉트
    """
    
    def __init__(self, real_stdout: TextIO, fallback: TextIO = None):
        self._real = real_stdout
        self._fallback = fallback or sys.stderr
        self._lock = threading.Lock()
        self._buffer = ""
        
        # 원본 stdout의 속성 복사
        self.encoding = getattr(real_stdout, 'encoding', 'utf-8')
        self.errors = getattr(real_stdout, 'errors', 'strict')
        self.newlines = getattr(real_stdout, 'newlines', None)
        self.mode = getattr(real_stdout, 'mode', 'w')
    
    def write(self, data: str) -> int:
        """
        데이터를 적절한 스트림으로 라우팅합니다.
        
        MCP 메시지 감지:
        - JSON-RPC: {"jsonrpc"... 로 시작
        - Content-Length 헤더
        """
        if not data:
            return 0
        
        # MCP 프로토콜 메시지 감지
        stripped = data.lstrip()
        is_mcp_message = (
            stripped.startswith('{"jsonrpc"') or
            stripped.startswith('Content-Length:') or
            stripped.startswith('content-length:')
        )
        
        if is_mcp_message:
            with self._lock:
                return self._real.write(data)
        
        # 비-MCP 메시지는 stderr로
        return self._fallback.write(data)
    
    def flush(self) -> None:
        """양쪽 스트림 모두 flush"""
        try:
            self._real.flush()
        except Exception:
            pass
        try:
            self._fallback.flush()
        except Exception:
            pass
    
    def fileno(self) -> int:
        """파일 디스크립터 반환 (실제 stdout 기준)"""
        return self._real.fileno()
    
    def isatty(self) -> bool:
        """TTY 여부"""
        return self._real.isatty()
    
    def readable(self) -> bool:
        return False
    
    def writable(self) -> bool:
        return True
    
    def seekable(self) -> bool:
        return False
    
    def close(self) -> None:
        """stdout은 닫지 않음 (시스템 스트림)"""
        pass
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass


def install_guard() -> StdoutGuard:
    """
    StdoutGuard를 설치하고 원본 stdout을 반환합니다.
    
    Usage:
        original_stdout = install_guard()
        # 이후 sys.stdout는 StdoutGuard로 보호됨
    """
    if isinstance(sys.stdout, StdoutGuard):
        # 이미 설치됨
        return sys.stdout._real
    
    original = sys.stdout
    guard = StdoutGuard(original, sys.stderr)
    sys.stdout = guard
    return original


def get_real_stdout() -> TextIO:
    """
    실제 stdout 핸들을 반환합니다 (MCP 메시지 직접 전송용).
    """
    if isinstance(sys.stdout, StdoutGuard):
        return sys.stdout._real
    return sys.stdout
