"""LSP Hub 확장자 매핑을 검증한다."""

import threading
import time
import os
import subprocess
import pytest

from sari.core.exceptions import DaemonError
from sari.core.language.registry import get_enabled_languages
from sari.lsp.hub import LspHub, LspRuntimeEntry, LspRuntimeKey
from sari.lsp.runtime_broker import RuntimeLaunchContextDTO, RuntimeRequirementDTO
from solidlsp.ls import get_current_process_env_snapshot
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


def test_lsp_hub_restores_global_ssl_cert_file_default_for_python_downloads(monkeypatch) -> None:
    """in-process HTTPS downloader를 위해 전역 SSL_CERT_FILE 기본값을 복원해야 한다."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    class _CertifiStub:
        @staticmethod
        def where() -> str:
            return "/tmp/certifi-ca.pem"

    monkeypatch.setattr("sari.lsp.hub.certifi", _CertifiStub)

    hub = LspHub()
    hub._ensure_global_ssl_cert_file_default()  # noqa: SLF001
    assert os.environ["SSL_CERT_FILE"] == "/tmp/certifi-ca.pem"

    monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")
    hub._ensure_global_ssl_cert_file_default()  # noqa: SLF001
    assert os.environ["SSL_CERT_FILE"] == "/custom/ca.pem"

    monkeypatch.setenv("SSL_CERT_FILE", "")
    hub._ensure_global_ssl_cert_file_default()  # noqa: SLF001
    assert os.environ["SSL_CERT_FILE"] == "/tmp/certifi-ca.pem"


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


def test_lsp_hub_scale_out_guard_blocks_additional_slot(monkeypatch) -> None:
    """profiled 언어 guard가 활성화되면 기존 인스턴스가 있는 상태에서 추가 scale-out은 차단되어야 한다."""

    class _FakeRuntimeServer:
        def is_running(self) -> bool:
            return True

    class _FakeLanguageServer:
        def __init__(self, idx: int) -> None:
            self.server = _FakeRuntimeServer()
            self.idx = idx

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

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
        max_instances_per_repo_language=3,
        hot_acquire_window_sec=5.0,
        scale_out_hot_hits=2,
        clock=_clock,
    )
    hub.set_scale_out_guard(lambda language, repo_root: language == Language.JAVA and repo_root.endswith("/repo-a"))

    first = hub.get_or_start(language=Language.JAVA, repo_root="/repo-a")
    now_state["ts"] = 0.2
    second = hub.get_or_start(language=Language.JAVA, repo_root="/repo-a")
    now_state["ts"] = 0.4
    third = hub.get_or_start(language=Language.JAVA, repo_root="/repo-a")

    assert first is second is third
    assert len(created) == 1
    metrics = hub.get_metrics()
    assert metrics["lsp_scale_out_guard_block_count"] >= 1
    hub.stop_all()


def test_lsp_hub_retention_touch_and_prune_protects_then_releases_idle_eviction(monkeypatch) -> None:
    """retention touch는 idle eviction을 연기하고 prune 후에는 다시 eviction 대상이 되어야 한다."""

    class _FakeRuntimeServer:
        def is_running(self) -> bool:
            return True

    class _FakeLanguageServer:
        def __init__(self) -> None:
            self.server = _FakeRuntimeServer()
            self.stopped = False

        def start(self) -> None:
            return None

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

    hub = LspHub(idle_timeout_sec=5, max_instances=8, clock=_clock)
    protected = hub.get_or_start(language=Language.JAVA, repo_root="/repo-protected")
    unprotected = hub.get_or_start(language=Language.JAVA, repo_root="/repo-unprotected")

    touched = hub.touch(
        language=Language.JAVA,
        repo_root="/repo-protected",
        ttl_override_sec=30.0,
        retention_tier="standby",
        hotness_score=10.0,
    )
    assert touched == 1

    now_state["ts"] = 10.0
    hub.get_or_start(language=Language.PYTHON, repo_root="/repo-trigger-evict")

    assert unprotected.stopped is True
    assert protected.stopped is False

    pruned = hub.prune_retention(language=Language.JAVA, keep_repo_roots=set(), retention_tier="standby")
    assert pruned >= 1
    now_state["ts"] = 20.0
    hub.get_or_start(language=Language.TYPESCRIPT, repo_root="/repo-trigger-evict-2")

    assert protected.stopped is True
    metrics = hub.get_metrics()
    assert metrics["lsp_retention_touch_count"] >= 1
    assert metrics["lsp_retention_prune_count"] >= 1
    hub.stop_all()


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


def test_lsp_hub_java_auto_fallback_retries_with_bundled_gradle(monkeypatch, tmp_path) -> None:
    """Java 시작 실패 시 wrapper-first 자동 fallback으로 1회 재시도해야 한다."""

    repo = tmp_path / "repo-java"
    wrapper_dir = repo / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.7-bin.zip\n",
        encoding="utf-8",
    )

    class _Runtime:
        def is_running(self) -> bool:
            return True

    class _FakeServer:
        def __init__(self, process_env: dict[str, str]) -> None:
            self.server = _Runtime()
            self._process_env = process_env

        def start(self) -> None:
            if self._process_env.get("SARI_JDTLS_GRADLE_WRAPPER_FIRST", "") != "0":
                raise RuntimeError("simulated jdtls wrapper-first failure")

        def stop(self) -> None:
            return None

    create_calls: list[str] = []

    def _fake_create(*args, **kwargs):  # noqa: ANN001, ANN201
        del args
        process_env = kwargs.get("process_env")
        assert isinstance(process_env, dict)
        create_calls.append(process_env.get("SARI_JDTLS_GRADLE_WRAPPER_FIRST", ""))
        return _FakeServer(process_env=process_env)

    monkeypatch.delenv("SARI_JDTLS_GRADLE_WRAPPER_FIRST", raising=False)
    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub()
    server = hub.get_or_start(language=Language.JAVA, repo_root=str(repo), request_kind="interactive")
    assert server.server.is_running() is True
    assert len(create_calls) == 2
    assert create_calls[0] == ""
    assert create_calls[1] == "0"
    hub.stop_all()


def test_lsp_hub_java_indexing_prefers_bundled_gradle_first(monkeypatch, tmp_path) -> None:
    """indexing 경로는 java 첫 기동에서 bundled gradle 우선 시도를 해야 한다."""

    repo = tmp_path / "repo-java-indexing"
    wrapper_dir = repo / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.7-bin.zip\n",
        encoding="utf-8",
    )

    class _Runtime:
        def is_running(self) -> bool:
            return True

    class _FakeServer:
        def __init__(self, process_env: dict[str, str]) -> None:
            self.server = _Runtime()
            self._process_env = process_env

        def start(self) -> None:
            if self._process_env.get("SARI_JDTLS_GRADLE_WRAPPER_FIRST", "") != "0":
                raise RuntimeError("indexing must prefer bundled gradle first")

        def stop(self) -> None:
            return None

    create_calls: list[str] = []

    def _fake_create(*args, **kwargs):  # noqa: ANN001, ANN201
        del args
        process_env = kwargs.get("process_env")
        assert isinstance(process_env, dict)
        create_calls.append(process_env.get("SARI_JDTLS_GRADLE_WRAPPER_FIRST", ""))
        return _FakeServer(process_env=process_env)

    monkeypatch.delenv("SARI_JDTLS_GRADLE_WRAPPER_FIRST", raising=False)
    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub()
    server = hub.get_or_start(language=Language.JAVA, repo_root=str(repo), request_kind="indexing")
    assert server.server.is_running() is True
    assert create_calls == ["0"]
    hub.stop_all()


def test_lsp_hub_java_explicit_wrapper_setting_disables_auto_fallback(monkeypatch, tmp_path) -> None:
    """wrapper 정책이 명시되면 자동 fallback 재시도를 수행하지 않는다."""

    repo = tmp_path / "repo-java-explicit"
    wrapper_dir = repo / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.7-bin.zip\n",
        encoding="utf-8",
    )

    class _Runtime:
        def is_running(self) -> bool:
            return True

    class _BrokenServer:
        def __init__(self) -> None:
            self.server = _Runtime()

        def start(self) -> None:
            raise RuntimeError("forced failure")

        def stop(self) -> None:
            return None

    create_calls = {"count": 0}

    def _fake_create(*args, **kwargs):  # noqa: ANN001, ANN201
        del args, kwargs
        create_calls["count"] += 1
        return _BrokenServer()

    monkeypatch.setenv("SARI_JDTLS_GRADLE_WRAPPER_FIRST", "1")
    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub()
    with pytest.raises(DaemonError):
        hub.get_or_start(language=Language.JAVA, repo_root=str(repo))
    assert create_calls["count"] == 1
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


def test_lsp_hub_applies_runtime_overrides_only_during_server_boot(monkeypatch) -> None:
    """런타임 오버라이드는 process_env snapshot으로 전달되고 전역 env는 오염되지 않아야 한다."""

    class _FakeRuntimeServer:
        def is_running(self) -> bool:
            return True

    class _FakeLanguageServer:
        def __init__(self) -> None:
            self.server = _FakeRuntimeServer()

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _FakeRuntimeBroker:
        def resolve(self, language: Language) -> RuntimeLaunchContextDTO:
            assert language == Language.JAVA
            return RuntimeLaunchContextDTO(
                requirement=RuntimeRequirementDTO(language=Language.JAVA, runtime_name="java", minimum_major=17),
                env_overrides={"JAVA_HOME": "/tmp/jdk-21", "PATH": "/tmp/jdk-21/bin:/usr/bin"},
                selected_executable="/tmp/jdk-21/bin/java",
                selected_major=21,
                selected_source="mock",
                auto_provision_expected=False,
            )

    original_java_home = os.environ.get("JAVA_HOME")

    captured_process_env: dict[str, str] = {}

    def _fake_create(*args, **kwargs) -> _FakeLanguageServer:
        del args
        process_env = kwargs.get("process_env")
        assert isinstance(process_env, dict)
        captured_process_env.update(process_env)
        assert process_env.get("JAVA_HOME") == "/tmp/jdk-21"
        assert os.environ.get("JAVA_HOME") == original_java_home
        return _FakeLanguageServer()

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)
    hub = LspHub(runtime_broker=_FakeRuntimeBroker())
    _ = hub.get_or_start(language=Language.JAVA, repo_root="/repo-a")
    hub.stop_all()

    assert os.environ.get("JAVA_HOME") == original_java_home
    assert captured_process_env["JAVA_HOME"] == "/tmp/jdk-21"
    assert captured_process_env["PATH"] == "/tmp/jdk-21/bin:/usr/bin"


def test_lsp_hub_max_instances_per_repo_language_not_clamped_to_two() -> None:
    """repo/language 풀 상한은 설정값을 그대로 반영해야 한다."""
    hub = LspHub(max_instances_per_repo_language=6)
    assert hub._max_instances_per_repo_language == 6
    hub.stop_all()


def test_lsp_hub_parallel_get_or_start_avoids_duplicate_start(monkeypatch) -> None:
    """동시 get_or_start에서도 동일 슬롯 중복 기동 없이 단일 인스턴스를 재사용해야 한다."""

    class _FakeRuntimeServer:
        def is_running(self) -> bool:
            return True

    class _FakeLanguageServer:
        def __init__(self) -> None:
            self.server = _FakeRuntimeServer()

        def start(self) -> None:
            time.sleep(0.05)

        def stop(self) -> None:
            return None

    created_count = {"value": 0}
    created_lock = threading.Lock()

    def _fake_create(*args, **kwargs) -> _FakeLanguageServer:
        del args, kwargs
        with created_lock:
            created_count["value"] += 1
        return _FakeLanguageServer()

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub(max_instances_per_repo_language=1, scale_out_hot_hits=128, request_timeout_sec=2.0)
    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            barrier.wait(timeout=1.0)
            results.append(hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a"))
        except BaseException as exc:  # pragma: no cover - 동시성 방어 경계
            errors.append(exc)

    first = threading.Thread(target=_worker, daemon=True)
    second = threading.Thread(target=_worker, daemon=True)
    first.start()
    second.start()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert len(errors) == 0
    assert len(results) == 2
    assert results[0] is results[1]
    assert created_count["value"] == 1
    hub.stop_all()


def test_lsp_hub_passes_file_buffer_settings_to_solidlsp(monkeypatch) -> None:
    """허브 설정으로 전달한 file-buffer 정책이 SolidLSP settings에 반영되어야 한다."""

    class _FakeRuntimeServer:
        def is_running(self) -> bool:
            return True

    class _FakeLanguageServer:
        def __init__(self) -> None:
            self.server = _FakeRuntimeServer()

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    captured: dict[str, object] = {}

    def _fake_create(*args, **kwargs) -> _FakeLanguageServer:
        del args
        captured["settings"] = kwargs.get("solidlsp_settings")
        return _FakeLanguageServer()

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)
    hub = LspHub(file_buffer_idle_ttl_sec=31.0, file_buffer_max_open=777)
    _ = hub.get_or_start(language=Language.PYTHON, repo_root="/repo-a")
    settings = captured["settings"]
    language_settings = settings.ls_specific_settings[Language.PYTHON]
    assert language_settings["open_file_buffer_idle_ttl_sec"] == pytest.approx(31.0)
    assert language_settings["open_file_buffer_max_open"] == 777
    hub.stop_all()


def test_lsp_hub_parallel_starts_use_isolated_process_env_snapshots(monkeypatch) -> None:
    """동시 기동 시에도 process_env snapshot이 섞이지 않고 전역 env는 유지되어야 한다."""

    class _FakeRuntimeServer:
        def is_running(self) -> bool:
            return True

    class _FakeLanguageServer:
        def __init__(self) -> None:
            self.server = _FakeRuntimeServer()

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _BrokerA:
        def resolve(self, language: Language) -> RuntimeLaunchContextDTO:
            assert language == Language.JAVA
            return RuntimeLaunchContextDTO(
                requirement=RuntimeRequirementDTO(language=Language.JAVA, runtime_name="java", minimum_major=17),
                env_overrides={"JAVA_HOME": "/tmp/jdk-a", "PATH": "/tmp/jdk-a/bin:/usr/bin"},
                selected_executable="/tmp/jdk-a/bin/java",
                selected_major=21,
                selected_source="mock-a",
                auto_provision_expected=False,
            )

    class _BrokerB:
        def resolve(self, language: Language) -> RuntimeLaunchContextDTO:
            assert language == Language.JAVA
            return RuntimeLaunchContextDTO(
                requirement=RuntimeRequirementDTO(language=Language.JAVA, runtime_name="java", minimum_major=17),
                env_overrides={"JAVA_HOME": "/tmp/jdk-b", "PATH": "/tmp/jdk-b/bin:/usr/bin"},
                selected_executable="/tmp/jdk-b/bin/java",
                selected_major=21,
                selected_source="mock-b",
                auto_provision_expected=False,
            )

    original_java_home = os.environ.get("JAVA_HOME")
    barrier = threading.Barrier(2)
    seen_java_homes: list[str] = []
    seen_globals: list[str | None] = []
    seen_lock = threading.Lock()

    def _fake_create(*args, **kwargs):  # noqa: ANN001, ANN201
        del args
        process_env = kwargs.get("process_env")
        assert isinstance(process_env, dict)
        barrier.wait(timeout=1.0)
        with seen_lock:
            seen_java_homes.append(str(process_env.get("JAVA_HOME")))
            seen_globals.append(os.environ.get("JAVA_HOME"))
        return _FakeLanguageServer()

    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub_a = LspHub(runtime_broker=_BrokerA())
    hub_b = LspHub(runtime_broker=_BrokerB())
    errors: list[BaseException] = []

    def _run(hub: LspHub, repo_root: str) -> None:
        try:
            _ = hub.get_or_start(language=Language.JAVA, repo_root=repo_root)
        except BaseException as exc:  # pragma: no cover - 동시성 방어 경계
            errors.append(exc)

    t1 = threading.Thread(target=_run, args=(hub_a, "/repo-a"), daemon=True)
    t2 = threading.Thread(target=_run, args=(hub_b, "/repo-b"), daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)
    hub_a.stop_all()
    hub_b.stop_all()

    assert errors == []
    assert sorted(seen_java_homes) == ["/tmp/jdk-a", "/tmp/jdk-b"]
    assert seen_globals == [original_java_home, original_java_home]


def test_lsp_hub_applies_attempt_process_env_during_start_call(monkeypatch, tmp_path) -> None:
    """fallback 재시도 플래그가 ls.start() 실행 중에도 process_env snapshot으로 보장되어야 한다."""
    repo = tmp_path / "repo-java"
    wrapper_dir = repo / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.7-bin.zip\n",
        encoding="utf-8",
    )

    class _Runtime:
        def is_running(self) -> bool:
            return True

    class _FakeServer:
        def __init__(self) -> None:
            self.server = _Runtime()

        def start(self) -> None:
            env = get_current_process_env_snapshot()
            if env.get("SARI_JDTLS_GRADLE_WRAPPER_FIRST", "") != "0":
                raise RuntimeError("simulated wrapper-first failure")

        def stop(self) -> None:
            return None

    create_calls: list[str] = []

    def _fake_create(*args, **kwargs):  # noqa: ANN001, ANN201
        del args
        process_env = kwargs.get("process_env")
        assert isinstance(process_env, dict)
        create_calls.append(process_env.get("SARI_JDTLS_GRADLE_WRAPPER_FIRST", ""))
        return _FakeServer()

    monkeypatch.delenv("SARI_JDTLS_GRADLE_WRAPPER_FIRST", raising=False)
    monkeypatch.setattr("sari.lsp.hub.SolidLanguageServer.create", _fake_create)

    hub = LspHub()
    server = hub.get_or_start(language=Language.JAVA, repo_root=str(repo), request_kind="indexing")
    hub.stop_all()

    assert server is not None
    assert create_calls == ["0"]
