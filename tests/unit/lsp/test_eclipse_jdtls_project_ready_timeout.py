import importlib
import os
from pathlib import Path
import threading


def _mod():
    import solidlsp.language_servers.eclipse_jdtls as m
    return m


def test_jdtls_project_ready_timeout_default(monkeypatch):
    monkeypatch.delenv('SARI_JDTLS_PROJECT_READY_TIMEOUT_SEC', raising=False)
    m = _mod()
    assert m._project_ready_timeout_seconds() == 20


def test_jdtls_project_ready_timeout_env_override(monkeypatch):
    monkeypatch.setenv('SARI_JDTLS_PROJECT_READY_TIMEOUT_SEC', '3')
    m = _mod()
    assert m._project_ready_timeout_seconds() == 3


def test_jdtls_project_ready_timeout_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv('SARI_JDTLS_PROJECT_READY_TIMEOUT_SEC', 'oops')
    m = _mod()
    assert m._project_ready_timeout_seconds() == 20


def test_jdtls_project_ready_timeout_negative_clamped(monkeypatch):
    monkeypatch.setenv('SARI_JDTLS_PROJECT_READY_TIMEOUT_SEC', '-1')
    m = _mod()
    assert m._project_ready_timeout_seconds() == 0


def test_jdtls_service_ready_timeout_defaults_to_unbounded_for_interactive(monkeypatch):
    monkeypatch.delenv('SARI_JDTLS_STARTUP_MODE', raising=False)
    monkeypatch.delenv('SARI_JDTLS_SERVICE_READY_TIMEOUT_SEC', raising=False)
    m = _mod()
    assert m._service_ready_timeout_seconds() == 0


def test_jdtls_service_ready_timeout_defaults_to_short_wait_for_indexing(monkeypatch):
    monkeypatch.setenv('SARI_JDTLS_STARTUP_MODE', 'indexing')
    monkeypatch.delenv('SARI_JDTLS_SERVICE_READY_TIMEOUT_SEC', raising=False)
    m = _mod()
    assert m._service_ready_timeout_seconds() == 2


def test_jdtls_intellicode_wait_timeout_defaults_to_short_wait_for_indexing(monkeypatch):
    monkeypatch.setenv('SARI_JDTLS_STARTUP_MODE', 'indexing')
    monkeypatch.delenv('SARI_JDTLS_INTELLICODE_WAIT_TIMEOUT_SEC', raising=False)
    m = _mod()
    assert m._intellicode_wait_timeout_seconds() == 1


def test_jdtls_initialize_params_uses_gradle_wrapper_by_default_when_wrapper_is_modern(monkeypatch, tmp_path: Path):
    monkeypatch.delenv('SARI_JDTLS_GRADLE_WRAPPER_FIRST', raising=False)
    m = _mod()
    jdtls = object.__new__(m.EclipseJDTLS)
    jdtls._custom_settings = {}
    jre_home = tmp_path / 'jre-home'
    jre_home.mkdir(parents=True)
    jdtls.runtime_dependency_paths = m.RuntimeDependencyPaths(
        gradle_path=str(tmp_path / 'gradle-8.14.2'),
        lombok_jar_path=str(tmp_path / 'lombok.jar'),
        jre_path=str(tmp_path / 'jre-home' / 'bin' / 'java'),
        jre_home_path=str(jre_home),
        jdtls_launcher_jar_path=str(tmp_path / 'launcher.jar'),
        jdtls_readonly_config_path=str(tmp_path / 'config'),
        intellicode_jar_path=str(tmp_path / 'intellicode.jar'),
        intellisense_members_path=str(tmp_path / 'members'),
    )

    wrapper_dir = tmp_path / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.7-bin.zip\n",
        encoding="utf-8",
    )

    params = jdtls._get_initialize_params(str(tmp_path))
    gradle = params["initializationOptions"]["settings"]["java"]["import"]["gradle"]

    assert gradle["wrapper"]["enabled"] is True
    assert "home" not in gradle
    assert gradle["java"].get("home") is None


def test_jdtls_initialize_params_can_disable_gradle_wrapper_first(monkeypatch, tmp_path: Path):
    monkeypatch.setenv('SARI_JDTLS_GRADLE_WRAPPER_FIRST', '0')
    m = _mod()
    jdtls = object.__new__(m.EclipseJDTLS)
    jdtls._custom_settings = {}
    jre_home = tmp_path / 'jre-home'
    jre_home.mkdir(parents=True)
    gradle_home = tmp_path / 'gradle-8.14.2'
    jdtls.runtime_dependency_paths = m.RuntimeDependencyPaths(
        gradle_path=str(gradle_home),
        lombok_jar_path=str(tmp_path / 'lombok.jar'),
        jre_path=str(tmp_path / 'jre-home' / 'bin' / 'java'),
        jre_home_path=str(jre_home),
        jdtls_launcher_jar_path=str(tmp_path / 'launcher.jar'),
        jdtls_readonly_config_path=str(tmp_path / 'config'),
        intellicode_jar_path=str(tmp_path / 'intellicode.jar'),
        intellisense_members_path=str(tmp_path / 'members'),
    )

    params = jdtls._get_initialize_params(str(tmp_path))
    gradle = params["initializationOptions"]["settings"]["java"]["import"]["gradle"]

    assert gradle["wrapper"]["enabled"] is False
    assert gradle["home"] == str(gradle_home)


def test_jdtls_initialize_params_uses_isolated_gradle_user_home_by_default(monkeypatch, tmp_path: Path):
    monkeypatch.delenv('SARI_JDTLS_GRADLE_USER_HOME_ISOLATED', raising=False)
    m = _mod()
    jdtls = object.__new__(m.EclipseJDTLS)
    jdtls._custom_settings = {}
    jre_home = tmp_path / 'jre-home'
    jre_home.mkdir(parents=True)
    jdtls._solidlsp_settings = type("S", (), {"ls_resources_dir": str(tmp_path / "ls_resources")})()
    jdtls.runtime_dependency_paths = m.RuntimeDependencyPaths(
        gradle_path=str(tmp_path / 'gradle-8.14.2'),
        lombok_jar_path=str(tmp_path / 'lombok.jar'),
        jre_path=str(tmp_path / 'jre-home' / 'bin' / 'java'),
        jre_home_path=str(jre_home),
        jdtls_launcher_jar_path=str(tmp_path / 'launcher.jar'),
        jdtls_readonly_config_path=str(tmp_path / 'config'),
        intellicode_jar_path=str(tmp_path / 'intellicode.jar'),
        intellisense_members_path=str(tmp_path / 'members'),
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    params = jdtls._get_initialize_params(str(repo))
    gradle = params["initializationOptions"]["settings"]["java"]["import"]["gradle"]
    assert gradle["user"]["home"] != str(Path.home() / ".gradle")
    assert ".isolated-gradle-home" in gradle["user"]["home"]


def test_dependency_provider_launch_env_includes_gradle_user_home(monkeypatch, tmp_path: Path):
    monkeypatch.delenv('SARI_JDTLS_GRADLE_USER_HOME_ISOLATED', raising=False)
    m = _mod()
    provider = object.__new__(m.EclipseJDTLS.DependencyProvider)
    provider._solidlsp_settings = type("S", (), {"ls_resources_dir": str(tmp_path / "ls_resources")})()
    provider._repository_root_path = str(tmp_path / "repo")
    provider.runtime_dependency_paths = m.RuntimeDependencyPaths(
        gradle_path=str(tmp_path / 'gradle-8.14.2'),
        lombok_jar_path=str(tmp_path / 'lombok.jar'),
        jre_path=str(tmp_path / 'jre-home' / 'bin' / 'java'),
        jre_home_path=str(tmp_path / 'jre-home'),
        jdtls_launcher_jar_path=str(tmp_path / 'launcher.jar'),
        jdtls_readonly_config_path=str(tmp_path / 'config'),
        intellicode_jar_path=str(tmp_path / 'intellicode.jar'),
        intellisense_members_path=str(tmp_path / 'members'),
    )
    env = provider.create_launch_command_env()
    assert "GRADLE_USER_HOME" in env
    assert ".isolated-gradle-home" in env["GRADLE_USER_HOME"]


def test_wait_project_ready_raises_when_required(monkeypatch):
    monkeypatch.setenv('SARI_JDTLS_REQUIRE_PROJECT_READY', '1')
    m = _mod()
    ev = threading.Event()
    try:
        m._wait_project_ready_or_raise(ev, timeout_sec=0)
        assert False, "expected RuntimeError when project-ready event is missing"
    except RuntimeError as exc:
        assert "Project readiness" in str(exc)


def test_wait_project_ready_can_continue_when_not_required(monkeypatch):
    monkeypatch.delenv('SARI_JDTLS_REQUIRE_PROJECT_READY', raising=False)
    m = _mod()
    ev = threading.Event()
    m._wait_project_ready_or_raise(ev, timeout_sec=0)


def test_wait_project_ready_skips_blocking_wait_when_not_required(monkeypatch):
    monkeypatch.setenv('SARI_JDTLS_REQUIRE_PROJECT_READY', '0')
    m = _mod()

    class _FakeEvent:
        def __init__(self) -> None:
            self.wait_calls = 0

        def wait(self, timeout=None):  # noqa: ANN001, ANN201
            self.wait_calls += 1
            return False

        def is_set(self) -> bool:
            return False

    ev = _FakeEvent()
    m._wait_project_ready_or_raise(ev, timeout_sec=120)
    assert ev.wait_calls == 0


def test_jdtls_auto_wrapper_disabled_when_wrapper_missing(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SARI_JDTLS_GRADLE_WRAPPER_FIRST", raising=False)
    m = _mod()
    jdtls = object.__new__(m.EclipseJDTLS)
    jdtls._custom_settings = {}
    jre_home = tmp_path / "jre-home"
    jre_home.mkdir(parents=True)
    gradle_home = tmp_path / "gradle-8.14.2"
    jdtls.runtime_dependency_paths = m.RuntimeDependencyPaths(
        gradle_path=str(gradle_home),
        lombok_jar_path=str(tmp_path / "lombok.jar"),
        jre_path=str(tmp_path / "jre-home" / "bin" / "java"),
        jre_home_path=str(jre_home),
        jdtls_launcher_jar_path=str(tmp_path / "launcher.jar"),
        jdtls_readonly_config_path=str(tmp_path / "config"),
        intellicode_jar_path=str(tmp_path / "intellicode.jar"),
        intellisense_members_path=str(tmp_path / "members"),
    )
    repo = tmp_path / "repo-no-wrapper"
    repo.mkdir()
    params = jdtls._get_initialize_params(str(repo))
    gradle = params["initializationOptions"]["settings"]["java"]["import"]["gradle"]
    assert gradle["wrapper"]["enabled"] is False
    assert gradle["home"] == str(gradle_home)


def test_jdtls_auto_wrapper_disabled_for_legacy_wrapper_version(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SARI_JDTLS_GRADLE_WRAPPER_FIRST", raising=False)
    m = _mod()
    jdtls = object.__new__(m.EclipseJDTLS)
    jdtls._custom_settings = {}
    jre_home = tmp_path / "jre-home"
    jre_home.mkdir(parents=True)
    gradle_home = tmp_path / "gradle-8.14.2"
    jdtls.runtime_dependency_paths = m.RuntimeDependencyPaths(
        gradle_path=str(gradle_home),
        lombok_jar_path=str(tmp_path / "lombok.jar"),
        jre_path=str(tmp_path / "jre-home" / "bin" / "java"),
        jre_home_path=str(jre_home),
        jdtls_launcher_jar_path=str(tmp_path / "launcher.jar"),
        jdtls_readonly_config_path=str(tmp_path / "config"),
        intellicode_jar_path=str(tmp_path / "intellicode.jar"),
        intellisense_members_path=str(tmp_path / "members"),
    )
    repo = tmp_path / "repo-old-wrapper"
    wrapper_dir = repo / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-6.9.4-bin.zip\n",
        encoding="utf-8",
    )
    params = jdtls._get_initialize_params(str(repo))
    gradle = params["initializationOptions"]["settings"]["java"]["import"]["gradle"]
    assert gradle["wrapper"]["enabled"] is False
    assert gradle["home"] == str(gradle_home)


def test_jdtls_auto_wrapper_enabled_for_modern_wrapper_version(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SARI_JDTLS_GRADLE_WRAPPER_FIRST", raising=False)
    m = _mod()
    jdtls = object.__new__(m.EclipseJDTLS)
    jdtls._custom_settings = {}
    jre_home = tmp_path / "jre-home"
    jre_home.mkdir(parents=True)
    jdtls.runtime_dependency_paths = m.RuntimeDependencyPaths(
        gradle_path=str(tmp_path / "gradle-8.14.2"),
        lombok_jar_path=str(tmp_path / "lombok.jar"),
        jre_path=str(tmp_path / "jre-home" / "bin" / "java"),
        jre_home_path=str(jre_home),
        jdtls_launcher_jar_path=str(tmp_path / "launcher.jar"),
        jdtls_readonly_config_path=str(tmp_path / "config"),
        intellicode_jar_path=str(tmp_path / "intellicode.jar"),
        intellisense_members_path=str(tmp_path / "members"),
    )
    repo = tmp_path / "repo-modern-wrapper"
    wrapper_dir = repo / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.7-bin.zip\n",
        encoding="utf-8",
    )
    params = jdtls._get_initialize_params(str(repo))
    gradle = params["initializationOptions"]["settings"]["java"]["import"]["gradle"]
    assert gradle["wrapper"]["enabled"] is True
    assert "home" not in gradle
