"""Top5 solidlsp 어댑터의 공통 유틸 사용 계약을 검증한다."""

from __future__ import annotations

import types
from pathlib import Path

import solidlsp.ls as ls_module
from solidlsp.ls import SolidLanguageServer, _describe_process_launch_info, process_env_context
from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.lsp_protocol_handler.server import ProcessLaunchInfo
from solidlsp.settings import SolidLSPSettings


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_top5_adapters_use_adapter_common_contract() -> None:
    """Top5 어댑터가 공통 모듈을 통해 명시적 경계 검사를 수행해야 한다."""
    root = Path(__file__).resolve().parents[2] / "src" / "solidlsp" / "language_servers"

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
    root = Path(__file__).resolve().parents[2] / "src" / "solidlsp" / "language_servers"
    csharp = _read(root / "csharp_language_server.py")
    assert "sync_with_ls: bool = True" in csharp


def test_document_symbols_overrides_keep_sync_hint_contract() -> None:
    """request_document_symbols override는 sync_with_ls 계약을 유지해야 한다."""
    root = Path(__file__).resolve().parents[2] / "src" / "solidlsp" / "language_servers"
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
