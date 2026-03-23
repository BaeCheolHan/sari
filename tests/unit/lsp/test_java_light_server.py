from __future__ import annotations

import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.settings import SolidLSPSettings


@pytest.fixture
def java_config() -> LanguageServerConfig:
    return LanguageServerConfig(code_language=Language.JAVA)


@pytest.fixture
def java_settings() -> SolidLSPSettings:
    return SolidLSPSettings()


def test_java_ls_defaults_to_javalight() -> None:
    ls_class = Language.JAVA.get_ls_class()

    assert ls_class.__name__ == "JavaLightServer"


def test_javalight_reports_java_language_enum(monkeypatch, tmp_path: Path) -> None:
    from solidlsp.language_servers.java_light_server import JavaLightServer

    home = tmp_path / "javalight-home"
    classpath_dir = home / "dist" / "classpath"
    classpath_dir.mkdir(parents=True)
    monkeypatch.setenv("SARI_JAVALIGHT_HOME", str(home))
    monkeypatch.setattr("solidlsp.language_servers.java_light_server.FileUtils.download_and_extract_archive", lambda *args, **kwargs: None)
    monkeypatch.setattr("solidlsp.language_servers.java_light_server.JavaLightServer.DependencyProvider._find_java_binary", lambda self, base_path: "java")

    server = JavaLightServer(LanguageServerConfig(code_language=Language.JAVA), str(tmp_path / "repo"), SolidLSPSettings())

    assert server.get_language_enum_instance() == Language.JAVA


def test_javalight_dependency_provider_launches_from_explicit_home(monkeypatch, tmp_path: Path) -> None:
    from solidlsp.language_servers.java_light_server import JavaLightServer

    home = tmp_path / "javalight-home"
    classpath_dir = home / "dist" / "classpath"
    classpath_dir.mkdir(parents=True)
    monkeypatch.setenv("SARI_JAVALIGHT_HOME", str(home))

    java_dir = tmp_path / "java21" / "jdk-21" / "bin"
    java_dir.mkdir(parents=True)
    java_bin = java_dir / "java"
    java_bin.write_text("", encoding="utf-8")

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path))
    provider.runtime_dependencies = {"jdk21": {}}

    with patch.object(provider, "_find_java_binary", return_value=str(java_bin)):
        with patch.object(provider, "_get_java_runtime_info", return_value=(21, True)):
            command = provider.create_launch_command()

    assert command[0] == str(java_bin)
    assert command[1:3] == ["-cp", str(classpath_dir / "*")]
    assert command[-1] == "org.javacs.Main"


def test_javalight_start_server_registers_protocol_handlers(monkeypatch, java_config: LanguageServerConfig, java_settings: SolidLSPSettings) -> None:
    from solidlsp.language_servers.java_light_server import JavaLightServer

    home = Path("/tmp/javalight-home")
    with patch.dict(os.environ, {"SARI_JAVALIGHT_HOME": str(home)}, clear=False):
        server = object.__new__(JavaLightServer)
        server.repository_root_path = "/tmp/repo"
        server.server = MagicMock()
        server.server.send.initialize.return_value = {"capabilities": {}}
        handlers: dict[str, object] = {}

        def _on_request(name: str, fn) -> None:  # noqa: ANN001
            handlers[name] = fn

        server.server.on_request.side_effect = _on_request

        server._start_server()

    assert handlers["workspace/configuration"]({"items": [{"section": "java"}, {"section": "java.home"}]}) == [{}, {}]
    assert handlers["workspace/workspaceFolders"]({}) == [
        {"uri": Path("/tmp/repo").as_uri(), "name": "repo"}
    ]
    server.server.start.assert_called_once()
    server.server.send.initialize.assert_called_once()


def test_javalight_dependency_provider_uses_managed_home_when_present(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SARI_JAVALIGHT_HOME", raising=False)
    from solidlsp.language_servers.java_light_server import JavaLightServer

    managed_home = tmp_path / "managed-javalight"
    classpath_dir = managed_home / "dist" / "classpath"
    classpath_dir.mkdir(parents=True)

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path))
    provider.runtime_dependencies = {"jdk21": {}}

    monkeypatch.setattr(provider, "_managed_server_home", lambda: str(managed_home))
    monkeypatch.setattr(provider, "_ensure_managed_jdk_home", lambda: str(tmp_path / "managed-jdk"))
    monkeypatch.setattr(provider, "_java_supports_compiler_modules", lambda java_bin: True)

    command = provider.create_launch_command()

    assert command[1:3] == ["-cp", str(classpath_dir / "*")]


def test_javalight_dependency_provider_bootstraps_managed_home_when_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SARI_JAVALIGHT_HOME", raising=False)
    from solidlsp.language_servers.java_light_server import JavaLightServer

    managed_home = tmp_path / "managed-javalight"
    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path))
    provider.runtime_dependencies = {"jdk21": {}}

    monkeypatch.setattr(provider, "_managed_server_home", lambda: str(managed_home))
    monkeypatch.setattr(provider, "_ensure_managed_jdk_home", lambda: str(tmp_path / "managed-jdk"))
    monkeypatch.setattr(provider, "_java_supports_compiler_modules", lambda java_bin: True)

    def _bootstrap(target_home: str) -> None:
        classpath_dir = Path(target_home) / "dist" / "classpath"
        classpath_dir.mkdir(parents=True)

    monkeypatch.setattr(provider, "_bootstrap_managed_server_home", _bootstrap)

    command = provider.create_launch_command()

    assert command[1:3] == ["-cp", str((managed_home / "dist" / "classpath") / "*")]


def test_javalight_dependency_provider_uses_bundled_bootstrap_when_env_and_local_source_are_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SARI_JAVALIGHT_HOME", raising=False)
    monkeypatch.delenv("SARI_JAVALIGHT_BOOTSTRAP_HOME", raising=False)
    from solidlsp.language_servers.java_light_server import JavaLightServer

    managed_home = tmp_path / "managed-javalight"
    bundled_home = tmp_path / "bundled-javalight"
    (bundled_home / "dist" / "classpath").mkdir(parents=True)
    (bundled_home / "dist" / "classpath" / "java-language-server.jar").write_text("jar", encoding="utf-8")

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path))
    provider.runtime_dependencies = {"jdk21": {}}

    monkeypatch.setattr(provider, "_managed_server_home", lambda: str(managed_home))
    monkeypatch.setattr(provider, "_ensure_managed_jdk_home", lambda: str(tmp_path / "managed-jdk"))
    monkeypatch.setattr(provider, "_java_supports_compiler_modules", lambda java_bin: True)
    monkeypatch.setattr(provider, "_bundled_server_home", lambda: str(bundled_home))
    monkeypatch.setattr("os.getcwd", lambda: str(tmp_path / "cwd-without-tools"))

    command = provider.create_launch_command()

    assert command[1:3] == ["-cp", str((managed_home / "dist" / "classpath") / "*")]
    assert (managed_home / "dist" / "classpath" / "java-language-server.jar").exists()


def test_javalight_dependency_provider_normalizes_tar_gz_archive_type(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SARI_JAVALIGHT_JAVA_HOME", raising=False)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    from solidlsp.language_servers.java_light_server import JavaLightServer

    home = tmp_path / "javalight-home"
    (home / "dist" / "classpath").mkdir(parents=True)
    monkeypatch.setenv("SARI_JAVALIGHT_HOME", str(home))

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path))
    provider.runtime_dependencies = {
        "jdk21": {
            "osx-arm64": {
                "url": "https://example.test/jre.tar.gz",
                "archiveType": "tar.gz",
                "relative_extraction_path": "java21",
            }
        }
    }

    captured: dict[str, str] = {}

    monkeypatch.setattr("solidlsp.language_servers.java_light_server.PlatformUtils.get_platform_id", lambda: "osx-arm64")
    monkeypatch.setattr("solidlsp.language_servers.java_light_server.JavaLightServer.DependencyProvider._find_java_binary", lambda self, base_path: "/tmp/java")
    probe_results = iter([False, True])
    monkeypatch.setattr(
        "solidlsp.language_servers.java_light_server.JavaLightServer.DependencyProvider._java_supports_compiler_modules",
        lambda self, java_bin: next(probe_results),
    )

    def _download(url: str, target_path: str, archive_type: str) -> None:
        captured["archive_type"] = archive_type
        Path(target_path).mkdir(parents=True)

    monkeypatch.setattr("solidlsp.language_servers.java_light_server.FileUtils.download_and_extract_archive", _download)

    provider.create_launch_command()

    assert captured["archive_type"] == "gztar"


def test_javalight_dependency_provider_prefers_explicit_java_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SARI_JAVALIGHT_JAVA_HOME", str(tmp_path / "explicit-jdk"))
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "env-jdk"))
    from solidlsp.language_servers.java_light_server import JavaLightServer

    home = tmp_path / "javalight-home"
    (home / "dist" / "classpath").mkdir(parents=True)
    monkeypatch.setenv("SARI_JAVALIGHT_HOME", str(home))

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path))
    provider.runtime_dependencies = {"jdk21": {}}

    explicit_java = tmp_path / "explicit-jdk" / "bin" / "java"
    explicit_java.parent.mkdir(parents=True)
    explicit_java.write_text("", encoding="utf-8")
    env_java = tmp_path / "env-jdk" / "bin" / "java"
    env_java.parent.mkdir(parents=True)
    env_java.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        provider,
        "_get_java_runtime_info",
        lambda java_bin: (21, True),
    )

    command = provider.create_launch_command()
    env = provider.create_launch_command_env()

    assert command[0] == str(explicit_java)
    assert env["JAVA_HOME"] == str(tmp_path / "explicit-jdk")


def test_javalight_dependency_provider_rejects_runtime_without_jdk_compiler(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SARI_JAVALIGHT_JAVA_HOME", str(tmp_path / "explicit-jre"))
    monkeypatch.delenv("JAVA_HOME", raising=False)
    from solidlsp.language_servers.java_light_server import JavaLightServer

    home = tmp_path / "javalight-home"
    (home / "dist" / "classpath").mkdir(parents=True)
    monkeypatch.setenv("SARI_JAVALIGHT_HOME", str(home))

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path))
    provider.runtime_dependencies = {"jdk21": {}}

    explicit_java = tmp_path / "explicit-jre" / "bin" / "java"
    explicit_java.parent.mkdir(parents=True)
    explicit_java.write_text("", encoding="utf-8")

    monkeypatch.setattr(provider, "_get_java_runtime_info", lambda java_bin: (None, False))
    monkeypatch.setattr(provider, "_ensure_managed_jdk_home", lambda: str(tmp_path / "managed-jdk"))
    monkeypatch.setattr(provider, "_find_java_binary", lambda base_path: "java")

    with pytest.raises(RuntimeError, match="jdk.compiler"):
        provider.create_launch_command()


def test_javalight_dependency_provider_uses_java_home_before_managed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SARI_JAVALIGHT_JAVA_HOME", raising=False)
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "env-jdk"))
    from solidlsp.language_servers.java_light_server import JavaLightServer

    home = tmp_path / "javalight-home"
    (home / "dist" / "classpath").mkdir(parents=True)
    monkeypatch.setenv("SARI_JAVALIGHT_HOME", str(home))

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path))
    provider.runtime_dependencies = {"jdk21": {}}

    env_java = tmp_path / "env-jdk" / "bin" / "java"
    env_java.parent.mkdir(parents=True)
    env_java.write_text("", encoding="utf-8")

    monkeypatch.setattr(provider, "_ensure_managed_jdk_home", lambda: str(tmp_path / "managed-jdk"))
    monkeypatch.setattr(provider, "_get_java_runtime_info", lambda java_bin: (21, True))

    command = provider.create_launch_command()
    env = provider.create_launch_command_env()

    assert command[0] == str(env_java)
    assert env["JAVA_HOME"] == str(tmp_path / "env-jdk")


def test_javalight_dependency_provider_checks_java_modules(monkeypatch, tmp_path: Path) -> None:
    from solidlsp.language_servers.java_light_server import JavaLightServer

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path))

    monkeypatch.setattr(
        "solidlsp.language_servers.java_light_server.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="java.compiler@21\njdk.compiler@21\n", stderr=""),
    )
    assert provider._java_supports_compiler_modules("/tmp/java") is True

    monkeypatch.setattr(
        "solidlsp.language_servers.java_light_server.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="java.compiler@21\n", stderr=""),
    )
    assert provider._java_supports_compiler_modules("/tmp/java") is False


def test_javalight_detects_required_java_version_from_maven_property(monkeypatch, tmp_path: Path) -> None:
    from solidlsp.language_servers.java_light_server import JavaLightServer

    (tmp_path / "pom.xml").write_text(
        """
        <project>
          <properties>
            <java.version>11</java.version>
          </properties>
        </project>
        """,
        encoding="utf-8",
    )
    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path), str(tmp_path))

    assert provider._detect_required_java_version() == 11


def test_javalight_detects_required_java_version_from_gradle_source_compatibility(monkeypatch, tmp_path: Path) -> None:
    from solidlsp.language_servers.java_light_server import JavaLightServer

    (tmp_path / "build.gradle").write_text("sourceCompatibility = '17'\n", encoding="utf-8")
    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path), str(tmp_path))

    assert provider._detect_required_java_version() == 17


def test_javalight_detects_required_java_version_from_gradle_kotlin_dsl(monkeypatch, tmp_path: Path) -> None:
    from solidlsp.language_servers.java_light_server import JavaLightServer

    (tmp_path / "build.gradle.kts").write_text(
        """
        java.sourceCompatibility = JavaVersion.VERSION_17
        tasks {
            compileKotlin {
                kotlinOptions { jvmTarget = "17" }
            }
        }
        """,
        encoding="utf-8",
    )
    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path), str(tmp_path))

    assert provider._detect_required_java_version() == 17


def test_javalight_dependency_provider_skips_env_java_home_if_version_too_low(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SARI_JAVALIGHT_JAVA_HOME", raising=False)
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "env-jdk11"))
    from solidlsp.language_servers.java_light_server import JavaLightServer

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "build.gradle").write_text("sourceCompatibility = '17'\n", encoding="utf-8")
    home = tmp_path / "javalight-home"
    (home / "dist" / "classpath").mkdir(parents=True)
    monkeypatch.setenv("SARI_JAVALIGHT_HOME", str(home))

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path), str(repo_root))
    provider.runtime_dependencies = {"jdk21": {}}

    env_java = tmp_path / "env-jdk11" / "bin" / "java"
    env_java.parent.mkdir(parents=True)
    env_java.write_text("", encoding="utf-8")
    managed_java = tmp_path / "managed-jdk21" / "bin" / "java"
    managed_java.parent.mkdir(parents=True)
    managed_java.write_text("", encoding="utf-8")

    monkeypatch.setattr(provider, "_ensure_managed_jdk_home", lambda: str(tmp_path / "managed-jdk21"))

    def _find_java_binary(base_path: str | None) -> str:
        if base_path == str(tmp_path / "env-jdk11"):
            return str(env_java)
        if base_path == str(tmp_path / "managed-jdk21"):
            return str(managed_java)
        return "java"

    monkeypatch.setattr(provider, "_find_java_binary", _find_java_binary)
    monkeypatch.setattr(
        provider,
        "_get_java_runtime_info",
        lambda java_bin: (11, True) if java_bin == str(env_java) else (21, True),
    )

    command = provider.create_launch_command()
    env = provider.create_launch_command_env()

    assert command[0] == str(managed_java)
    assert env["JAVA_HOME"] == str(tmp_path / "managed-jdk21")


def test_javalight_dependency_provider_rejects_all_runtimes_below_required_version(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SARI_JAVALIGHT_JAVA_HOME", str(tmp_path / "explicit-jdk11"))
    monkeypatch.delenv("JAVA_HOME", raising=False)
    from solidlsp.language_servers.java_light_server import JavaLightServer

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "build.gradle").write_text("sourceCompatibility = '17'\n", encoding="utf-8")
    home = tmp_path / "javalight-home"
    (home / "dist" / "classpath").mkdir(parents=True)
    monkeypatch.setenv("SARI_JAVALIGHT_HOME", str(home))

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path), str(repo_root))
    provider.runtime_dependencies = {"jdk21": {}}

    explicit_java = tmp_path / "explicit-jdk11" / "bin" / "java"
    explicit_java.parent.mkdir(parents=True)
    explicit_java.write_text("", encoding="utf-8")

    monkeypatch.setattr(provider, "_ensure_managed_jdk_home", lambda: str(tmp_path / "managed-jdk11"))

    def _find_java_binary(base_path: str | None) -> str:
        if base_path == str(tmp_path / "explicit-jdk11"):
            return str(explicit_java)
        if base_path == str(tmp_path / "managed-jdk11"):
            return str(tmp_path / "managed-jdk11" / "bin" / "java")
        return "java"

    monkeypatch.setattr(provider, "_find_java_binary", _find_java_binary)
    monkeypatch.setattr(provider, "_get_java_runtime_info", lambda java_bin: (11, True))

    with pytest.raises(RuntimeError, match="JDK >= 17"):
        provider.create_launch_command()


def test_javalight_find_java_binary_prefers_jdk_over_jre_sibling(monkeypatch, tmp_path: Path) -> None:
    from solidlsp.language_servers.java_light_server import JavaLightServer

    settings = SolidLSPSettings(solidlsp_dir=str(tmp_path / ".solidlsp-global"))
    provider = JavaLightServer.DependencyProvider(settings.get_ls_specific_settings(Language.JAVA), str(tmp_path))

    jre_java = tmp_path / "java21" / "jdk-21.0.2+13-jre" / "Contents" / "Home" / "bin" / "java"
    jre_java.parent.mkdir(parents=True)
    jre_java.write_text("", encoding="utf-8")
    jdk_java = tmp_path / "java21" / "jdk-21.0.2+13" / "Contents" / "Home" / "bin" / "java"
    jdk_java.parent.mkdir(parents=True)
    jdk_java.write_text("", encoding="utf-8")

    java_bin = provider._find_java_binary(str(tmp_path / "java21"))

    assert java_bin == str(jdk_java)
