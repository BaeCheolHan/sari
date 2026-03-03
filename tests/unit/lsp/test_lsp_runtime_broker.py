"""LSP 런타임 브로커 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from solidlsp.ls_config import Language

from sari.lsp.runtime_broker import LspRuntimeBroker, RuntimeLaunchContextDTO


def test_runtime_broker_uses_highest_compatible_java_runtime(monkeypatch) -> None:
    """Java 후보 중 최소 버전 이상인 런타임을 우선 선택해야 한다."""
    broker = LspRuntimeBroker()
    candidates = [("mock", Path("/jdk11/bin/java")), ("mock", Path("/jdk21/bin/java")), ("mock", Path("/jdk17/bin/java"))]

    monkeypatch.setattr(broker, "_candidate_java_executables", lambda: candidates)
    monkeypatch.setattr(
        broker,
        "_probe_java_major",
        lambda java_executable: 21 if "21" in str(java_executable) else (17 if "17" in str(java_executable) else 11),
    )

    context = broker.resolve(Language.JAVA)

    assert isinstance(context, RuntimeLaunchContextDTO)
    assert context.selected_executable == "/jdk21/bin/java"
    assert context.selected_major == 21
    assert context.auto_provision_expected is False
    assert context.env_overrides.get("JAVA_HOME") == "/jdk21"


def test_runtime_broker_permissive_fallback_uses_best_available_java(monkeypatch) -> None:
    """호환 Java가 없으면 permissive 정책으로 가능한 최고 버전을 사용한다."""
    monkeypatch.setenv("SARI_LSP_BUNDLED_JRE_DOWNLOAD_ENABLED", "0")
    broker = LspRuntimeBroker()

    monkeypatch.setattr(broker, "_candidate_java_executables", lambda: [("mock", Path("/jdk11/bin/java"))])
    monkeypatch.setattr(broker, "_probe_java_major", lambda java_executable: 11)

    context = broker.resolve(Language.KOTLIN)

    assert context.selected_executable == "/jdk11/bin/java"
    assert context.selected_major == 11
    assert context.auto_provision_expected is False
    assert context.env_overrides.get("JAVA_HOME") == "/jdk11"


def test_runtime_broker_scala_uses_java_resolution(monkeypatch) -> None:
    """Scala도 Java 런타임 해석 대상이어야 한다."""
    broker = LspRuntimeBroker()

    monkeypatch.setattr(broker, "_candidate_java_executables", lambda: [("mock", Path("/jdk21/bin/java"))])
    monkeypatch.setattr(broker, "_probe_java_major", lambda java_executable: 21)

    context = broker.resolve(Language.SCALA)

    assert context.selected_executable == "/jdk21/bin/java"
    assert context.selected_major == 21
    assert context.auto_provision_expected is False
    assert context.env_overrides.get("JAVA_HOME") == "/jdk21"


def test_runtime_broker_go_uses_gopath_bin_when_home_is_isolated(monkeypatch) -> None:
    """HOME이 격리되어도 GOPATH/bin 후보를 PATH에 반영해야 한다."""
    broker = LspRuntimeBroker()

    monkeypatch.setenv("GOPATH", "/opt/go-workspace")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr("sari.lsp.runtime_broker.Path.home", lambda: Path("/tmp/isolated-home"))
    monkeypatch.setattr("sari.lsp.runtime_broker.Path.exists", lambda self: str(self) in {"/opt/go-workspace/bin", "/tmp/isolated-home/go/bin"})

    class _Result:
        returncode = 0
        stdout = "/opt/go-workspace\n"

    monkeypatch.setattr("sari.lsp.runtime_broker.subprocess.run", lambda *args, **kwargs: _Result())

    context = broker.resolve(Language.GO)
    resolved_path = context.env_overrides.get("PATH", "")
    assert resolved_path.startswith("/opt/go-workspace/bin")
