"""
StdoutGuard: MCP 프로토콜 보호를 위한 stdout 래퍼

서드파티 라이브러리가 stdout에 직접 출력하면 MCP JSON-RPC 프로토콜이 오염됩니다.
이 모듈은 MCP 메시지만 실제 stdout으로 전달하고, 나머지는 stderr로 리다이렉트합니다.
"""
import sys
import threading
from typing import TextIO


class StdoutGuard:
    """
    MCP 프로토콜 보호용 stdout 래퍼.
    
    - Sari의 공식 Transport 레이어는 get_real_stdout()을 통해 
      이 가드를 직접 우회하여 원본 stdout으로 통신합니다.
    - 따라서 이 가드로 들어오는 데이터는 대부분 '비공식 출력(Noise)'이며,
      매우 확실한 프로토콜 메시지(Content-Length 등)가 아니면 모두 stderr로 격리합니다.
    """
    
    def __init__(self, real_stdout: TextIO, fallback: TextIO = None):
        self._real = real_stdout
        self._fallback = fallback or sys.stderr
        self._lock = threading.Lock()

        # Binary-safe passthrough
        self.buffer = getattr(real_stdout, "buffer", None)
        self.encoding = getattr(real_stdout, 'encoding', 'utf-8')
        self.errors = getattr(real_stdout, 'errors', 'strict')
        self.mode = getattr(real_stdout, 'mode', 'w')
    
    def write(self, data: str) -> int:
        if not data:
            return 0

        # bytes 입력 허용
        if isinstance(data, (bytes, bytearray)):
            with self._lock:
                if self.buffer is not None:
                    return self.buffer.write(data)
                try:
                    data = data.decode(self.encoding or "utf-8", errors=self.errors or "strict")
                except Exception:
                    data = data.decode("utf-8", errors="replace")
        
        stripped = data.strip()
        
        # 매우 엄격한 프로토콜 감지 (실수로 print된 데이터 차단용)
        # 공식 채널은 이 로직을 타지 않으므로 여기서의 엄격함은 통신 안정성에 도움이 됨
        is_mcp_message = (
            stripped.lower().startswith("content-length:") or
            (stripped.startswith('{"jsonrpc":"2.0"') and stripped.endswith('}'))
        )
        
        if is_mcp_message:
            with self._lock:
                return self._real.write(data)
        
        # 나머지는 모두 stderr로 (CLI가 프로토콜 에러를 내지 않도록 보호)
        return self._fallback.write(data)
    
    def flush(self) -> None:
        """양쪽 스트림 모두 flush"""
        try:
            self._real.flush()
        except Exception:
            pass
        if self.buffer is not None:
            try:
                self.buffer.flush()
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
