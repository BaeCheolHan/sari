"""Top5 solidlsp 어댑터의 공통 유틸 사용 계약을 검증한다."""

from __future__ import annotations

import types
from pathlib import Path
import os

import solidlsp.ls as ls_module
from solidlsp.ls import SolidLanguageServer, _describe_process_launch_info, process_env_context
from solidlsp.ls_exceptions import SolidLSPException
from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.lsp_protocol_handler.server import ProcessLaunchInfo
from solidlsp.settings import SolidLSPSettings


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_top5_adapters_use_adapter_common_contract() -> None:
    """Top5 어댑터가 공통 모듈을 통해 명시적 경계 검사를 수행해야 한다."""
    root = Path(__file__).resolve().parents[3] / "src" / "solidlsp" / "language_servers"

    vue = _read(root / "vue_language_server.py")
    csharp = _read(root / "csharp_language_server.py")
    pascal = _read(root / "pascal_server.py")
    jdtls = _read(root / "eclipse_jdtls.py")
    rust = _read(root / "rust_analyzer.py")

    assert "ensure_commands_available" in vue
    assert "ensure_paths_exist" in csharp
    assert "ensure_paths_exist" in pascal
    assert "ensure_paths_exist" in jdtls
    assert "first_executable_path" in rust


def test_csharp_adapter_document_symbols_signature_supports_sync_hint() -> None:
    """C# 어댑터 override는 base 계약(sync_with_ls)을 유지해야 한다."""
    root = Path(__file__).resolve().parents[3] / "src" / "solidlsp" / "language_servers"
    csharp = _read(root / "csharp_language_server.py")
    assert "sync_with_ls: bool = True" in csharp


def test_document_symbols_overrides_keep_sync_hint_contract() -> None:
    """request_document_symbols override는 sync_with_ls 계약을 유지해야 한다."""
    root = Path(__file__).resolve().parents[3] / "src" / "solidlsp" / "language_servers"
    for rel in (
        "bash_language_server.py",
        "nixd_ls.py",
        "al_language_server.py",
        "fortran_language_server.py",
    ):
        content = _read(root / rel)
        assert "sync_with_ls: bool = True" in content


class _ExplicitLaunchInfoTestServer(SolidLanguageServer):
    @classmethod
    def get_language_enum_instance(cls) -> Language:
        return Language.PYTHON

    def __init__(self, config: LanguageServerConfig, repository_root_path: str, solidlsp_settings: SolidLSPSettings) -> None:
        super().__init__(
            config=config,
            repository_root_path=repository_root_path,
            process_launch_info=ProcessLaunchInfo(
                cmd=["dummy-ls"],
                cwd=repository_root_path,
                env={"PATH": "/explicit/bin", "PERL5LIB": "/explicit/perl"},
            ),
            language_id="python",
            solidlsp_settings=solidlsp_settings,
        )

    def _start_server(self) -> None:
        raise NotImplementedError


def test_explicit_process_launch_info_inherits_process_env_snapshot(monkeypatch, tmp_path: Path) -> None:
    """명시적 ProcessLaunchInfo 경로도 create snapshot env를 하위 프로세스로 전달해야 한다."""
    captured: list[ProcessLaunchInfo] = []

    class _DummyHandler:
        def __init__(self, process_launch_info: ProcessLaunchInfo, *args, **kwargs) -> None:
            del args, kwargs
            captured.append(process_launch_info)
            self.process_launch_info = process_launch_info
            self.notify = types.SimpleNamespace()
            self.send = types.SimpleNamespace()

    monkeypatch.setattr(ls_module, "SolidLanguageServerHandler", _DummyHandler)
    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    config = LanguageServerConfig(code_language=Language.PYTHON)

    with process_env_context({"JAVA_HOME": "/tmp/jdk-21", "PATH": "/snapshot/bin"}):
        _ = _ExplicitLaunchInfoTestServer(config, str(tmp_path / "repo"), settings)

    assert len(captured) == 1
    env = captured[0].env
    assert env["JAVA_HOME"] == "/tmp/jdk-21"
    assert env["PERL5LIB"] == "/explicit/perl"
    assert env["PATH"] == "/explicit/bin"


def test_process_launch_info_debug_summary_redacts_env_values() -> None:
    """디버그 요약 문자열은 env 값/전체 argv를 그대로 노출하지 않아야 한다."""
    info = ProcessLaunchInfo(
        cmd=["java", "-Dtoken=secret-value", "-jar", "server.jar"],
        cwd="/tmp/repo",
        env={"API_TOKEN": "secret-value", "PATH": "/bin"},
    )

    summary = _describe_process_launch_info(info)

    assert "secret-value" not in summary
    assert "-Dtoken=secret-value" not in summary
    assert "env_keys=2" in summary


def test_python_ls_uses_pyrefly_by_default() -> None:
    """Python 기본 adapter는 Pyrefly여야 한다."""
    ls_class = Language.PYTHON.get_ls_class()

    assert ls_class.__name__ == "PyreflyServer"


def test_pyrefly_dependency_provider_launches_pyrefly_lsp(monkeypatch, tmp_path: Path) -> None:
    """Pyrefly adapter는 pyrefly lsp 명령으로 서버를 기동해야 한다."""
    from solidlsp.language_servers.pyrefly_server import PyreflyServer

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    config = LanguageServerConfig(code_language=Language.PYTHON)
    provider = PyreflyServer.DependencyProvider(settings.get_ls_specific_settings(Language.PYTHON), str(tmp_path))

    command = provider.create_launch_command()

    assert os.path.basename(command[0]) == "pyrefly"
    assert command[1:] == ["lsp"]


def test_pyrefly_dependency_provider_supports_indexing_mode_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SARI_PYREFLY_INDEXING_MODE", "lazy-blocking")
    from solidlsp.language_servers.pyrefly_server import PyreflyServer

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = PyreflyServer.DependencyProvider(settings.get_ls_specific_settings(Language.PYTHON), str(tmp_path))

    command = provider.create_launch_command()

    assert os.path.basename(command[0]) == "pyrefly"
    assert command[1:] == ["lsp", "--indexing-mode", "lazy-blocking"]


def test_pyrefly_document_symbols_retries_once_after_subsequent_mutation(monkeypatch) -> None:
    from solidlsp.language_servers.pyrefly_server import PyreflyServer

    calls = {"count": 0}

    def _fake_request(self, relative_file_path: str, file_buffer=None, *, sync_with_ls: bool = True):  # noqa: ANN001
        del self, relative_file_path, file_buffer, sync_with_ls
        calls["count"] += 1
        if calls["count"] == 1:
            raise SolidLSPException("Error processing request textDocument/documentSymbol (caused by Request textDocument/documentSymbol (2) is canceled due to subsequent mutation (-32800))")
        return "ok"

    monkeypatch.setattr(SolidLanguageServer, "request_document_symbols", _fake_request)
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)

    server = object.__new__(PyreflyServer)
    result = server.request_document_symbols("x.py")

    assert result == "ok"
    assert calls["count"] == 2


def test_pyrefly_references_retry_once_after_subsequent_mutation(monkeypatch) -> None:
    from solidlsp.language_servers.pyrefly_server import PyreflyServer

    calls = {"count": 0}

    def _fake_refs(self, relative_file_path: str, line: int, column: int):  # noqa: ANN001
        del self, relative_file_path, line, column
        calls["count"] += 1
        if calls["count"] == 1:
            raise SolidLSPException("Error processing request textDocument/references (caused by Request textDocument/references (2) is canceled due to subsequent mutation (-32800))")
        return ["ok"]

    monkeypatch.setattr(SolidLanguageServer, "request_references", _fake_refs)
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)

    server = object.__new__(PyreflyServer)
    server._primed_reference_paths = {"x.py"}
    result = server.request_references("x.py", 1, 1)

    assert result == ["ok"]
    assert calls["count"] == 2


def test_pyrefly_references_prime_document_symbols_once_before_first_request(monkeypatch) -> None:
    from solidlsp.language_servers.pyrefly_server import PyreflyServer

    calls: list[tuple[str, str]] = []

    def _fake_document_symbols(self, relative_file_path: str, file_buffer=None, *, sync_with_ls: bool = True):  # noqa: ANN001
        del self, file_buffer, sync_with_ls
        calls.append(("document_symbols", relative_file_path))
        return "symbols"

    def _fake_refs(self, relative_file_path: str, line: int, column: int):  # noqa: ANN001
        del self, line, column
        calls.append(("references", relative_file_path))
        return ["ok"]

    monkeypatch.setattr(SolidLanguageServer, "request_document_symbols", _fake_document_symbols)
    monkeypatch.setattr(SolidLanguageServer, "request_references", _fake_refs)

    server = object.__new__(PyreflyServer)
    server._primed_reference_paths = set()

    first = server.request_references("x.py", 1, 1)
    second = server.request_references("x.py", 1, 1)

    assert first == ["ok"]
    assert second == ["ok"]
    assert calls == [
        ("document_symbols", "x.py"),
        ("references", "x.py"),
        ("references", "x.py"),
    ]
