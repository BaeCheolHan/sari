"""LSP Hub 확장자 매핑을 검증한다."""

import time
import subprocess
import pytest

from sari.core.exceptions import DaemonError
from sari.core.language_registry import get_enabled_languages
from sari.lsp.hub import LspHub, LspRuntimeEntry, LspRuntimeKey
from solidlsp.ls_config import Language


def test_lsp_hub_resolve_language_success() -> None:
    """지원 확장자에 대해 언어 매핑이 정상 동작하는지 확인한다."""
    hub = LspHub()
    assert hub.resolve_language("a.py") == Language.PYTHON
    assert hub.resolve_language("a.ts") == Language.TYPESCRIPT
    assert hub.resolve_language("a.java") == Language.JAVA
    assert hub.resolve_language("a.kt") == Language.KOTLIN
    assert hub.resolve_language("a.go") == Language.GO
    assert hub.resolve_language("a.rs") == Language.RUST
    assert hub.resolve_language("a.cpp") == Language.CPP
    assert hub.resolve_language("a.cs") == Language.CSHARP
    assert hub.resolve_language("a.swift") == Language.SWIFT
    assert hub.resolve_language("a.php") == Language.PHP
    assert hub.resolve_language("a.vue") == Language.VUE
    assert hub.resolve_language("a.toml") == Language.TOML


def test_lsp_hub_resolve_language_unsupported() -> None:
    """미지원 확장자에 대해 명시적 오류를 반환하는지 확인한다."""
    hub = LspHub()
    with pytest.raises(DaemonError, match="지원하지 않는 언어 확장자입니다"):
        hub.resolve_language("a.unknown")


def test_language_registry_supports_many_languages() -> None:
    """운영 레지스트리는 최소 35개 언어를 활성화해야 한다."""
    enabled = get_enabled_languages()
    assert len(enabled) >= 35


def test_lsp_hub_stop_all_raises_explicit_error_when_stop_fails() -> None:
    """LSP 종료 실패는 로그만 남기지 않고 명시적 DaemonError로 올라와야 한다."""

    class _FailingServer:
        """stop 호출 시 실패를 발생시키는 테스트 더블이다."""

        class _Runtime:
            """실행 상태 인터페이스를 제공한다."""

            def is_running(self) -> bool:
                """항상 실행 중으로 간주한다."""
                return True

        def __init__(self) -> None:
            """런타임 핸들을 초기화한다."""
            self.server = self._Runtime()

        def stop(self) -> None:
            """종료 실패를 시뮬레이션한다."""
            raise RuntimeError("stop failed")

    hub = LspHub()
    hub._instances[LspRuntimeKey(language=Language.PYTHON, repo_root="/tmp/repo", slot=0)] = LspRuntimeEntry(
        server=_FailingServer(),
        last_used_at=0.0,
    )

    with pytest.raises(DaemonError) as exc_info:
        hub.stop_all()

    assert exc_info.value.context.code == "ERR_LSP_STOP_FAILED"
    assert len(hub._instances) == 0


def test_lsp_hub_evicts_idle_instances(monkeypatch) -> None:
    """idle timeout을 넘긴 인스턴스는 다음 요청 전에 정리되어야 한다."""

    class _FakeRuntimeServer:
        """is_running 응답을 제공하는 서버 더블이다."""

        def is_running(self) -> bool:
            """항상 실행 중을 반환한다."""
            return True

    class _FakeLanguageServer:
        """시작/종료 호출을 추적하는 LSP 더블이다."""

        def __init__(self) -> None:
            """상태를 초기화한다."""
            self.server = _FakeRuntimeServer()
            self.started = False
            self.stopped = False

        def start(self) -> None:
            """시작 플래그를 기록한다."""
            self.started = True

        def stop(self) -> None:
            """종료 플래그를 기록한다."""
            self.stopped = True

    created: list[_FakeLanguageServer] = []

    def _fake_create(*args, **kwargs) -> _FakeLanguageServer:
        del args, kwargs
        server = _FakeLanguageServer()
        created.append(server)
        return server

    now_state = {"ts": 0.0}

    def _clock() -> float:
        return float(now_state["ts"])

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub(idle_timeout_sec=5, max_instances=8, clock=_clock)
    first = hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")

    now_state["ts"] = 10.0
    hub.get_or_start(language=Language.TYPESCRIPT, repo_root="/repo-b")

    assert first.stopped is True
    assert len(created) == 2


def test_lsp_hub_evicts_lru_when_max_instances_exceeded(monkeypatch) -> None:
    """최대 인스턴스 초과 시 LRU 인스턴스를 종료해야 한다."""

    class _FakeRuntimeServer:
        """is_running 응답을 제공하는 서버 더블이다."""

        def is_running(self) -> bool:
            """항상 실행 중을 반환한다."""
            return True

    class _FakeLanguageServer:
        """시작/종료 호출을 추적하는 LSP 더블이다."""

        def __init__(self) -> None:
            """상태를 초기화한다."""
            self.server = _FakeRuntimeServer()
            self.started = False
            self.stopped = False

        def start(self) -> None:
            """시작 플래그를 기록한다."""
            self.started = True

        def stop(self) -> None:
            """종료 플래그를 기록한다."""
            self.stopped = True

    created: list[_FakeLanguageServer] = []

    def _fake_create(*args, **kwargs) -> _FakeLanguageServer:
        del args, kwargs
        server = _FakeLanguageServer()
        created.append(server)
        return server

    now_state = {"ts": 0.0}

    def _clock() -> float:
        return float(now_state["ts"])

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub(idle_timeout_sec=60, max_instances=1, clock=_clock)
    first = hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")

    now_state["ts"] = 1.0
    hub.get_or_start(language=Language.TYPESCRIPT, repo_root="/repo-b")

    assert first.stopped is True
    assert len(created) == 2


def test_lsp_hub_passes_request_timeout_to_server_create(monkeypatch) -> None:
    """LSP 인스턴스 생성 시 request timeout 설정이 전달되어야 한다."""

    class _FakeRuntimeServer:
        def is_running(self) -> bool:
            return True

    class _FakeLanguageServer:
        def __init__(self) -> None:
            self.server = _FakeRuntimeServer()

        def start(self) -> None:
            """시작 호출을 허용한다."""

        def stop(self) -> None:
            """종료 호출을 허용한다."""

    captured: dict[str, float | None] = {"timeout": None}

    def _fake_create(*args, **kwargs) -> _FakeLanguageServer:
        del args
        captured["timeout"] = kwargs.get("timeout")
        return _FakeLanguageServer()

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub(request_timeout_sec=12.5)
    _ = hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")
    hub.stop_all()

    assert captured["timeout"] == 12.5


def test_lsp_hub_scales_out_hot_path_and_round_robins(monkeypatch) -> None:
    """짧은 간격 재요청에서는 동일 언어/레포 풀을 2개까지 확장하고 RR로 분산해야 한다."""

    class _FakeRuntimeServer:
        """is_running 응답을 제공하는 서버 더블이다."""

        def is_running(self) -> bool:
            """항상 실행 중을 반환한다."""
            return True

    class _FakeLanguageServer:
        """서버 식별자를 보관하는 더블이다."""

        def __init__(self, idx: int) -> None:
            self.server = _FakeRuntimeServer()
            self.idx = idx

        def start(self) -> None:
            """시작 호출을 허용한다."""

        def stop(self) -> None:
            """종료 호출을 허용한다."""

    created: list[_FakeLanguageServer] = []

    def _fake_create(*args, **kwargs) -> _FakeLanguageServer:
        del args, kwargs
        server = _FakeLanguageServer(len(created))
        created.append(server)
        return server

    now_state = {"ts": 0.0}

    def _clock() -> float:
        return float(now_state["ts"])

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub(
        idle_timeout_sec=60,
        max_instances=8,
        max_instances_per_repo_language=2,
        hot_acquire_window_sec=1.0,
        scale_out_hot_hits=2,
        clock=_clock,
    )
    first = hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")
    now_state["ts"] = 0.2
    second = hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")

    assert first is not second
    assert len(created) == 2

    now_state["ts"] = 2.0
    rr1 = hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")
    rr2 = hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")
    assert rr1 is not rr2


def test_lsp_hub_prewarm_starts_all_slots_immediately(monkeypatch) -> None:
    """prewarm 호출 시 대상 언어/저장소 풀을 즉시 목표 슬롯 수까지 띄워야 한다."""

    class _FakeRuntimeServer:
        """is_running 응답을 제공하는 서버 더블이다."""

        def is_running(self) -> bool:
            """항상 실행 중을 반환한다."""
            return True

    class _FakeLanguageServer:
        """시작/종료 상태를 기록하는 LSP 더블이다."""

        def __init__(self) -> None:
            self.server = _FakeRuntimeServer()
            self.started = False

        def start(self) -> None:
            """시작 플래그를 기록한다."""
            self.started = True

        def stop(self) -> None:
            """종료를 허용한다."""

    created: list[_FakeLanguageServer] = []

    def _fake_create(*args, **kwargs) -> _FakeLanguageServer:
        del args, kwargs
        server = _FakeLanguageServer()
        created.append(server)
        return server

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub(max_instances_per_repo_language=2)
    hub.prewarm_language_pool(language=Language.PYTHON, repo_root="/repo-a")


def test_lsp_hub_scale_out_respects_global_soft_limit(monkeypatch) -> None:
    """전역 소프트 상한 도달 시 scale-out은 차단되고 기존 인스턴스를 재사용해야 한다."""

    class _FakeRuntimeServer:
        def is_running(self) -> bool:
            return True

    class _FakeLanguageServer:
        def __init__(self, idx: int) -> None:
            self.server = _FakeRuntimeServer()
            self.idx = idx

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    created: list[_FakeLanguageServer] = []

    def _fake_create(*args, **kwargs) -> _FakeLanguageServer:
        del args, kwargs
        server = _FakeLanguageServer(len(created))
        created.append(server)
        return server

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub(
        max_instances=8,
        max_instances_per_repo_language=2,
        scale_out_hot_hits=2,
        lsp_global_soft_limit=1,
    )
    first = hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")
    second = hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")

    assert first is second
    assert len(created) == 1
    hub.prewarm_language_pool(language=Language.PYTHON, repo_root="/repo-a")

    assert len(created) == 2
    assert all(server.started for server in created)


def test_lsp_hub_background_idle_cleaner_evicts_without_new_request(monkeypatch) -> None:
    """신규 요청이 없어도 백그라운드 cleaner가 idle 인스턴스를 정리해야 한다."""

    class _FakeRuntimeServer:
        def is_running(self) -> bool:
            return True

    class _FakeLanguageServer:
        def __init__(self) -> None:
            self.server = _FakeRuntimeServer()
            self.stopped = False

        def start(self) -> None:
            pass

        def stop(self) -> None:
            self.stopped = True

    created: list[_FakeLanguageServer] = []

    def _fake_create(*args, **kwargs) -> _FakeLanguageServer:
        del args, kwargs
        server = _FakeLanguageServer()
        created.append(server)
        return server

    now_state = {"ts": 0.0}

    def _clock() -> float:
        return float(now_state["ts"])

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub(idle_timeout_sec=1, idle_cleanup_interval_sec=0.05, clock=_clock)
    hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")
    now_state["ts"] = 10.0
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if created[0].stopped:
            break
        time.sleep(0.02)

    assert len(created) == 1
    assert created[0].stopped is True
    hub.stop_all()


def test_lsp_hub_next_slot_raises_when_pool_is_full() -> None:
    """풀 슬롯이 모두 사용 중이면 신규 슬롯 할당은 명시 오류를 반환해야 한다."""

    class _RunningServer:
        class _Runtime:
            def is_running(self) -> bool:
                return True

        def __init__(self) -> None:
            self.server = self._Runtime()

        def stop(self) -> None:
            return None

    hub = LspHub(max_instances_per_repo_language=2)
    hub._instances[LspRuntimeKey(language=Language.PYTHON, repo_root="/repo", slot=0)] = LspRuntimeEntry(
        server=_RunningServer(),
        last_used_at=0.0,
    )
    hub._instances[LspRuntimeKey(language=Language.PYTHON, repo_root="/repo", slot=1)] = LspRuntimeEntry(
        server=_RunningServer(),
        last_used_at=0.0,
    )

    with pytest.raises(DaemonError) as exc_info:
        hub._next_slot_locked(language=Language.PYTHON, repo_root="/repo")
    assert exc_info.value.context.code == "ERR_LSP_SLOT_EXHAUSTED"
    hub.stop_all()


def test_lsp_hub_stop_timeout_raises_explicit_error() -> None:
    """stop이 타임아웃되면 명시적 오류 코드로 승격되어야 한다."""

    class _BlockingServer:
        class _Runtime:
            def is_running(self) -> bool:
                return True

        def __init__(self) -> None:
            self.server = self._Runtime()

        def stop(self) -> None:
            time.sleep(0.35)

    hub = LspHub(stop_timeout_sec=0.05)
    key = LspRuntimeKey(language=Language.PYTHON, repo_root="/repo", slot=0)
    hub._instances[key] = LspRuntimeEntry(server=_BlockingServer(), last_used_at=0.0)

    with pytest.raises(DaemonError) as exc_info:
        hub._stop_entry_locked(key)
    assert exc_info.value.context.code == "ERR_LSP_STOP_TIMEOUT"
    metrics = hub.get_metrics()
    assert metrics["lsp_stop_timeout_count"] >= 1
    hub._instances.clear()
    hub.stop_all()


def test_lsp_hub_stop_timeout_forces_kill_process_group(monkeypatch) -> None:
    """stop 타임아웃 시 하위 프로세스 그룹에 강제 종료 신호를 보내야 한다."""

    class _BlockingServer:
        class _Runtime:
            def __init__(self) -> None:
                self.process = self
                self.pid = 32123

            def is_running(self) -> bool:
                return True

        def __init__(self) -> None:
            self.server = self._Runtime()

        def stop(self) -> None:
            time.sleep(0.3)

    kill_calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr("sari.lsp.hub.os.getpgid", lambda pid: pid)
    monkeypatch.setattr("sari.lsp.hub.os.killpg", lambda pgid, sig: kill_calls.append(("pgid", pgid, int(sig))))
    monkeypatch.setattr("sari.lsp.hub.os.kill", lambda pid, sig: kill_calls.append(("pid", pid, int(sig))))

    hub = LspHub(stop_timeout_sec=0.05)
    key = LspRuntimeKey(language=Language.PYTHON, repo_root="/repo", slot=0)
    hub._instances[key] = LspRuntimeEntry(server=_BlockingServer(), last_used_at=0.0)

    with pytest.raises(DaemonError) as exc_info:
        hub._stop_entry_locked(key)
    assert exc_info.value.context.code == "ERR_LSP_STOP_TIMEOUT"
    assert ("pgid", 32123, 15) in kill_calls
    assert ("pgid", 32123, 9) in kill_calls


def test_lsp_hub_maps_assertion_error_to_explicit_unavailable(monkeypatch) -> None:
    """언어서버 start 내부 assertion 실패는 명시적 LSP unavailable 오류로 승격되어야 한다."""

    class _BrokenServer:
        class _Runtime:
            def is_running(self) -> bool:
                return False

        def __init__(self) -> None:
            self.server = self._Runtime()

        def start(self) -> None:
            raise AssertionError("broken initialize capability assertion")

        def stop(self) -> None:
            return None

    def _fake_create(*args, **kwargs) -> _BrokenServer:
        del args, kwargs
        return _BrokenServer()

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub()
    with pytest.raises(DaemonError) as exc_info:
        hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")
    assert exc_info.value.context.code == "ERR_LSP_UNAVAILABLE"
    hub.stop_all()


def test_lsp_hub_restart_if_unhealthy_uses_stop_timeout_guard(monkeypatch) -> None:
    """restart_if_unhealthy도 stop timeout 가드를 통해 비정상 서버를 정리해야 한다."""

    class _BlockingServer:
        class _Runtime:
            def is_running(self) -> bool:
                return False

        def __init__(self) -> None:
            self.server = self._Runtime()

        def stop(self) -> None:
            time.sleep(0.3)

    class _RunningServer:
        class _Runtime:
            def is_running(self) -> bool:
                return True

        def __init__(self) -> None:
            self.server = self._Runtime()

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    def _fake_create(*args, **kwargs) -> _RunningServer:
        del args, kwargs
        return _RunningServer()

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub(stop_timeout_sec=0.05)
    key = LspRuntimeKey(language=Language.PYTHON, repo_root="/repo", slot=0)
    hub._instances[key] = LspRuntimeEntry(server=_BlockingServer(), last_used_at=0.0)

    restarted = hub.restart_if_unhealthy(language=Language.PYTHON, repo_root="/repo")

    assert restarted.server.is_running() is True
    hub.stop_all()


def test_lsp_hub_cleans_up_not_running_entry_before_reuse(monkeypatch) -> None:
    """재사용 전 is_running=false 엔트리를 stop 시도 후 정리해야 한다."""

    class _StoppedRuntime:
        def is_running(self) -> bool:
            return False

    class _StoppedServer:
        def __init__(self) -> None:
            self.server = _StoppedRuntime()
            self.stop_called = False

        def stop(self) -> None:
            self.stop_called = True

    class _RunningRuntime:
        def is_running(self) -> bool:
            return True

    class _RunningServer:
        def __init__(self) -> None:
            self.server = _RunningRuntime()

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    def _fake_create(*args, **kwargs) -> _RunningServer:
        del args, kwargs
        return _RunningServer()

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    stale_server = _StoppedServer()
    hub = LspHub()
    key = LspRuntimeKey(language=Language.PYTHON, repo_root=str("/repo"), slot=0)
    hub._instances[key] = LspRuntimeEntry(server=stale_server, last_used_at=time.monotonic())

    _ = hub.get_or_start(language=Language.PYTHON, repo_root="/repo")

    assert stale_server.stop_called is True
    metrics = hub.get_metrics()
    assert metrics["lsp_orphan_suspect_count"] >= 1
    hub.stop_all()


def test_lsp_hub_fails_fast_when_java_runtime_is_too_old(monkeypatch) -> None:
    """Java/Kotlin LSP는 런타임 요구사항 미충족 시 즉시 명시 오류를 반환해야 한다."""

    class _Result:
        def __init__(self, stderr: str) -> None:
            self.stderr = stderr
            self.stdout = ""

    monkeypatch.setattr(
        "sari.lsp.hub.subprocess.run",
        lambda *args, **kwargs: _Result(stderr='openjdk version "11.0.24" 2024-07-16\n'),
    )

    hub = LspHub()
    with pytest.raises(DaemonError) as exc_info:
        hub.get_or_start(language=Language.JAVA, repo_root="/repo-a")

    assert exc_info.value.context.code == "ERR_LSP_RUNTIME_MISMATCH"
    assert "Java 17+" in exc_info.value.context.message


def test_lsp_hub_runtime_probe_failure_raises_explicit_error(monkeypatch) -> None:
    """런타임 probe 실패 시 침묵하지 않고 명시 오류를 반환해야 한다."""

    def _raise_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        raise OSError("java not found")

    monkeypatch.setattr("sari.lsp.hub.subprocess.run", _raise_run)

    hub = LspHub()
    with pytest.raises(DaemonError) as exc_info:
        hub.get_or_start(language=Language.KOTLIN, repo_root="/repo-a")

    assert exc_info.value.context.code == "ERR_LSP_RUNTIME_PROBE_FAILED"
