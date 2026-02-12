import os
from sari.core.daemon_resolver import resolve_daemon_address, DEFAULT_PORT, DEFAULT_HOST
import sari.core.daemon_resolver as resolver_mod
from sari.core.server_registry import ServerRegistry

def test_resolve_default():
    """No env, no registry -> Default."""
    # Ensure env is clean
    os.environ.pop("SARI_DAEMON_HOST", None)
    os.environ.pop("SARI_DAEMON_PORT", None)
    os.environ.pop("SARI_DAEMON_OVERRIDE", None)
    
    host, port = resolve_daemon_address("/tmp/fake-root")
    assert host == DEFAULT_HOST
    assert port == DEFAULT_PORT

def test_resolve_env_fallback_without_registry_entry(monkeypatch, tmp_path):
    """Env set, no matching registry entry -> env fallback."""
    monkeypatch.setenv("SARI_REGISTRY_FILE", str(tmp_path / "server.json"))
    os.environ["SARI_DAEMON_PORT"] = "55555"
    host, port = resolve_daemon_address("/tmp/fake-root")
    assert port == 55555
    os.environ.pop("SARI_DAEMON_PORT")


def test_resolve_env_override_wins():
    """Env override flag forces env daemon endpoint."""
    os.environ["SARI_DAEMON_PORT"] = "55555"
    os.environ["SARI_DAEMON_OVERRIDE"] = "1"
    host, port = resolve_daemon_address("/tmp/fake-root")
    assert port == 55555
    os.environ.pop("SARI_DAEMON_PORT")
    os.environ.pop("SARI_DAEMON_OVERRIDE")

def test_resolve_registry_priority(monkeypatch, tmp_path):
    """Registry should beat Env fallback unless override is set."""
    # 1. Setup Registry
    reg_file = tmp_path / "server.json"
    monkeypatch.setenv("SARI_REGISTRY_FILE", str(reg_file))
    
    registry = ServerRegistry()
    ws_root = str(tmp_path / "ws")
    boot_id = "test-boot"
    registry.register_daemon(boot_id, "127.0.0.1", 44444, os.getpid())
    registry.set_workspace(ws_root, boot_id)
    
    # 2. Set Env Fallback (lower priority)
    monkeypatch.setenv("SARI_DAEMON_PORT", "55555")
    
    # 3. Resolve
    host, port = resolve_daemon_address(ws_root)
    assert port == 44444 # Should pick from registry
    
    # 4. Test Override (highest priority)
    monkeypatch.setenv("SARI_DAEMON_OVERRIDE", "1")
    host, port = resolve_daemon_address(ws_root)
    assert port == 55555 # Should pick from env now


def test_resolver_exposes_failure_state_on_registry_exception(monkeypatch):
    monkeypatch.setattr("sari.core.daemon_resolver.ServerRegistry", lambda: (_ for _ in ()).throw(RuntimeError("resolver boom")))
    monkeypatch.delenv("SARI_DAEMON_PORT", raising=False)
    monkeypatch.delenv("SARI_DAEMON_OVERRIDE", raising=False)

    host, port = resolve_daemon_address("/tmp/fake-root")
    status = resolver_mod.get_last_resolver_status()

    assert host == DEFAULT_HOST
    assert port == DEFAULT_PORT
    assert status["resolver_ok"] is False
    assert "resolver boom" in str(status.get("error", ""))
