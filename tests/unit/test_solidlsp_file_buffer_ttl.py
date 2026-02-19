"""open_file buffer TTL/LRU 정책을 검증한다."""

from __future__ import annotations

import time
from pathlib import Path

from solidlsp.ls import SolidLanguageServer


class _DummyLanguageServer:
    """open_file 동작만 검증하기 위한 최소 더블이다."""

    open_file = SolidLanguageServer.open_file
    _evict_open_file_buffers = SolidLanguageServer._evict_open_file_buffers

    def __init__(self, repo_root: str, ttl_sec: float, max_open: int) -> None:
        self.server_started = True
        self.repository_root_path = repo_root
        self._encoding = "utf-8"
        self.open_file_buffers: dict[str, object] = {}
        self._open_file_buffer_idle_ttl_sec = ttl_sec
        self._open_file_buffer_max_open = max_open

    def _get_language_id_for_file(self, relative_file_path: str) -> str:
        del relative_file_path
        return "python"


def test_open_file_does_not_close_immediately_when_ref_count_zero(tmp_path: Path) -> None:
    """ref_count가 0이어도 즉시 didClose/remove하지 않고 버퍼를 유지해야 한다."""
    file_path = tmp_path / "a.py"
    file_path.write_text("print('a')\n", encoding="utf-8")
    server = _DummyLanguageServer(repo_root=str(tmp_path), ttl_sec=60.0, max_open=16)

    with server.open_file("a.py", open_in_ls=False):
        pass

    assert len(server.open_file_buffers) == 1


def test_open_file_eviction_runs_after_ttl(tmp_path: Path) -> None:
    """TTL이 지난 유휴 버퍼는 다음 open_file 진입 시 정리되어야 한다."""
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("print('a')\n", encoding="utf-8")
    second.write_text("print('b')\n", encoding="utf-8")
    server = _DummyLanguageServer(repo_root=str(tmp_path), ttl_sec=0.01, max_open=16)

    with server.open_file("a.py", open_in_ls=False):
        pass
    time.sleep(0.02)
    with server.open_file("b.py", open_in_ls=False):
        pass

    assert len(server.open_file_buffers) == 1
