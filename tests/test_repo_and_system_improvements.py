from sari.core.utils import system as system_utils
from sari.mcp.tools.read_file import _apply_pagination


def test_failed_task_repository_count_uses_single_aggregate_query(monkeypatch, db):
    repo = db.tasks
    calls = []

    class _Cur:
        def fetchone(self):
            return {"total": 5, "high": 2}

    def _fake_execute(sql, params=None):
        calls.append(sql)
        return _Cur()

    monkeypatch.setattr(repo, "execute", _fake_execute)

    total, high = repo.count_failed_tasks()
    assert total == 5
    assert high == 2
    assert len(calls) == 1
    assert "SUM(CASE WHEN attempts >= 3 THEN 1 ELSE 0 END)" in calls[0]


def test_list_sari_processes_does_not_request_memory_info_in_iterator(monkeypatch):
    observed_attrs = {}

    class _FakePsutil:
        class NoSuchProcess(Exception):
            pass

        class AccessDenied(Exception):
            pass

        @staticmethod
        def process_iter(attrs):
            observed_attrs["attrs"] = attrs
            return []

    monkeypatch.setattr(system_utils, "psutil", _FakePsutil)
    monkeypatch.setattr(system_utils.os, "getpid", lambda: 99999)

    out = system_utils.list_sari_processes()
    assert out == []
    assert "memory_info" not in observed_attrs["attrs"]


def test_kill_sari_process_waits_and_falls_back_to_kill(monkeypatch):
    class _Proc:
        def __init__(self):
            self.terminated = False
            self.killed = False
            self.wait_calls = 0
            self.running = True

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise TimeoutError("still running")
            self.running = False
            return None

        def kill(self):
            self.killed = True

        def is_running(self):
            return self.running

    proc = _Proc()
    monkeypatch.setattr(system_utils.os, "getpid", lambda: 99999)
    monkeypatch.setattr(system_utils.psutil, "Process", lambda _pid: proc)

    assert system_utils.kill_sari_process(12345) is True
    assert proc.terminated is True
    assert proc.killed is True


def test_apply_pagination_avoids_splitlines_materialization():
    class _NoSplitLines(str):
        def splitlines(self, *args, **kwargs):  # pragma: no cover - should never be called
            raise AssertionError("splitlines should not be called")

    content = _NoSplitLines("a\nb\nc\n")
    out = _apply_pagination(content, offset=1, limit=1)
    assert out["content"] == "b"
    assert out["total_lines"] == 3
    assert out["is_truncated"] is True
    assert out["next_offset"] == 2
