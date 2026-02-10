# Strict Single Daemon Upgrade Policy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure Sari always runs exactly one daemon instance, and on version updates it replaces the old daemon instead of spawning additional daemon processes on new ports.

**Architecture:** Centralize daemon endpoint defaults in one source, enforce singleton behavior in daemon start/ensure flows, and make stop/status operate on registry-wide daemon state by default. Upgrade handling will be explicit: detect version mismatch or draining, gracefully mark old daemon draining, then stop and relaunch at the canonical endpoint.

**Tech Stack:** Python 3.13, existing Sari CLI modules (`sari.mcp.cli.*`), registry SSOT (`sari.core.server_registry`), pytest.

---

### Task 1: Lock Daemon Port Defaults to a Single Source

**Files:**
- Modify: `src/sari/core/constants.py`
- Modify: `src/sari/core/settings.py`
- Modify: `src/sari/core/daemon_resolver.py`
- Modify: `src/sari/mcp/daemon.py`
- Modify: `src/sari/mcp/cli/daemon.py`
- Test: `tests/test_daemon_singleton_defaults.py`

**Step 1: Write the failing test**

```python
from sari.core.constants import DEFAULT_DAEMON_PORT
from sari.core import daemon_resolver
import sari.mcp.daemon as daemon_mod
import sari.mcp.cli.daemon as cli_daemon


def test_daemon_defaults_are_consistent():
    assert daemon_resolver.DEFAULT_PORT == DEFAULT_DAEMON_PORT
    assert daemon_mod.DEFAULT_PORT == DEFAULT_DAEMON_PORT
    assert cli_daemon.DEFAULT_PORT == DEFAULT_DAEMON_PORT
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_singleton_defaults.py::test_daemon_defaults_are_consistent -v`
Expected: FAIL due to current mismatch (`47765/47779/47800`).

**Step 3: Write minimal implementation**

- Replace local hardcoded daemon defaults with `DEFAULT_DAEMON_PORT` import.
- Ensure `settings.DAEMON_PORT` default is aligned with constants.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_singleton_defaults.py::test_daemon_defaults_are_consistent -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/sari/core/constants.py src/sari/core/settings.py src/sari/core/daemon_resolver.py src/sari/mcp/daemon.py src/sari/mcp/cli/daemon.py tests/test_daemon_singleton_defaults.py
git commit -m "fix: unify daemon default port across modules"
```

### Task 2: Enforce Strict Singleton in Start Path (No Free-Port Fallback)

**Files:**
- Modify: `src/sari/mcp/cli/daemon.py`
- Test: `tests/test_daemon_singleton_start.py`

**Step 1: Write the failing test**

```python
from types import SimpleNamespace
import sari.mcp.cli.daemon as d


def test_start_does_not_switch_to_free_port_when_target_busy(monkeypatch):
    params = {
        "host": "127.0.0.1",
        "port": 47779,
        "explicit_port": False,
        "registry": SimpleNamespace(find_free_port=lambda start_port: 47790),
    }
    monkeypatch.setattr(d, "is_port_in_use", lambda h, p: True)

    rc = d.check_port_availability(params)

    assert rc == 1
    assert params["port"] == 47779
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_singleton_start.py::test_start_does_not_switch_to_free_port_when_target_busy -v`
Expected: FAIL because current code rewrites port to free port.

**Step 3: Write minimal implementation**

- In `check_port_availability`, remove automatic free-port reassignment.
- Return hard failure if canonical endpoint is occupied and not reclaimable.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_singleton_start.py::test_start_does_not_switch_to_free_port_when_target_busy -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/sari/mcp/cli/daemon.py tests/test_daemon_singleton_start.py
git commit -m "fix: enforce singleton daemon endpoint without free-port fallback"
```

### Task 3: Replace-on-Upgrade Flow (Version Mismatch => Drain/Stop/Restart)

**Files:**
- Modify: `src/sari/mcp/cli/daemon.py`
- Modify: `src/sari/mcp/cli/smart_daemon.py`
- Test: `tests/test_daemon_upgrade_replace.py`

**Step 1: Write the failing test**

```python
import argparse
import sari.mcp.cli.daemon as d


def test_version_mismatch_triggers_stop_then_restart(monkeypatch):
    params = {
        "host": "127.0.0.1",
        "port": 47779,
        "workspace_root": "/tmp/ws",
        "registry": object(),
        "explicit_port": False,
        "force_start": False,
        "args": argparse.Namespace(),
    }
    calls = []
    monkeypatch.setattr(d, "identify_sari_daemon", lambda h, p: {"version": "0.6.9", "draining": False})
    monkeypatch.setattr(d, "get_local_version", lambda: "0.6.10")
    monkeypatch.setattr(d, "cmd_daemon_stop", lambda a: calls.append((a.daemon_host, a.daemon_port)) or 0)

    rc = d.handle_existing_daemon(params)

    assert rc is None
    assert calls == [("127.0.0.1", 47779)]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_upgrade_replace.py::test_version_mismatch_triggers_stop_then_restart -v`
Expected: FAIL because current behavior switches to free port.

**Step 3: Write minimal implementation**

- In `handle_existing_daemon`: on upgrade/drain detection, stop current endpoint and continue startup on same endpoint.
- In `ensure_smart_daemon`: if identity version mismatches local, invoke replacement routine instead of unconditional reuse.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_upgrade_replace.py::test_version_mismatch_triggers_stop_then_restart -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/sari/mcp/cli/daemon.py src/sari/mcp/cli/smart_daemon.py tests/test_daemon_upgrade_replace.py
git commit -m "feat: replace stale daemon on upgrade at canonical endpoint"
```

### Task 4: Make `daemon stop` Registry-Wide by Default

**Files:**
- Modify: `src/sari/mcp/cli/daemon.py`
- Modify: `src/sari/mcp/cli/legacy_cli.py`
- Test: `tests/test_daemon_stop_all.py`

**Step 1: Write the failing test**

```python
import argparse
import sari.mcp.cli.daemon as d


def test_stop_without_endpoint_stops_all_registry_daemons(monkeypatch):
    killed = []
    monkeypatch.setattr(d, "list_registry_daemon_endpoints", lambda: [("127.0.0.1", 47779), ("127.0.0.1", 47790)])
    monkeypatch.setattr(d, "stop_one_endpoint", lambda h, p: killed.append((h, p)) or 0)

    rc = d.stop_daemon_process({"host": None, "port": None, "all": True})

    assert rc == 0
    assert killed == [("127.0.0.1", 47779), ("127.0.0.1", 47790)]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_stop_all.py::test_stop_without_endpoint_stops_all_registry_daemons -v`
Expected: FAIL because current stop targets single resolver endpoint.

**Step 3: Write minimal implementation**

- Add registry-wide stop branch as default behavior for `daemon stop` when endpoint not explicitly pinned.
- Keep `--daemon-host/--daemon-port` as scoped-stop override.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_stop_all.py::test_stop_without_endpoint_stops_all_registry_daemons -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/sari/mcp/cli/daemon.py src/sari/mcp/cli/legacy_cli.py tests/test_daemon_stop_all.py
git commit -m "feat: stop all registered daemon instances by default"
```

### Task 5: Expose Multi-Daemon Visibility in Status

**Files:**
- Modify: `src/sari/mcp/cli/daemon.py`
- Modify: `src/sari/mcp/cli/legacy_cli.py`
- Test: `tests/test_daemon_status_list.py`

**Step 1: Write the failing test**

```python
import sari.mcp.cli.legacy_cli as cli


def test_daemon_status_lists_all_daemons(monkeypatch, capsys):
    monkeypatch.setattr(cli, "list_registry_daemons", lambda: [
        {"host": "127.0.0.1", "port": 47779, "pid": 1001, "version": "0.6.10"},
        {"host": "127.0.0.1", "port": 47790, "pid": 1002, "version": "0.6.9"},
    ])

    cli.cmd_daemon_status(type("A", (), {})())
    out = capsys.readouterr().out

    assert "47779" in out and "47790" in out
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_status_list.py::test_daemon_status_lists_all_daemons -v`
Expected: FAIL because current output is single endpoint summary.

**Step 3: Write minimal implementation**

- Add status output mode that prints all live registry daemons and highlights resolved active target.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_status_list.py::test_daemon_status_lists_all_daemons -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/sari/mcp/cli/daemon.py src/sari/mcp/cli/legacy_cli.py tests/test_daemon_status_list.py
git commit -m "feat: show all live daemons in daemon status"
```

### Task 6: Integration Regression for Codex + Gemini Concurrent Usage

**Files:**
- Create: `tests/test_daemon_singleton_integration.py`

**Step 1: Write the failing test**

```python
def test_two_client_ensure_paths_reuse_single_daemon():
    # Simulate codex then gemini ensure/start paths, verify one endpoint survives.
    # (Use monkeypatch for identify/probe/registry to avoid real process spawning.)
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_singleton_integration.py::test_two_client_ensure_paths_reuse_single_daemon -v`
Expected: FAIL under current multi-daemon-capable logic.

**Step 3: Write minimal implementation**

- Adjust shared ensure/start path helpers until both clients converge on one endpoint.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon_singleton_integration.py::test_two_client_ensure_paths_reuse_single_daemon -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_daemon_singleton_integration.py
git commit -m "test: add singleton integration regression for multi-client startup"
```

### Task 7: Verification and Documentation

**Files:**
- Modify: `README.md`
- Modify: `README_KR.md`
- Modify: `docs/TROUBLESHOOTING.md`

**Step 1: Update docs for singleton policy**

- Document upgrade behavior: old daemon replaced, not parallelized.
- Document stop semantics: default all-daemon stop and scoped stop option.

**Step 2: Run full targeted verification**

Run:
```bash
pytest -v \
  tests/test_daemon_singleton_defaults.py \
  tests/test_daemon_singleton_start.py \
  tests/test_daemon_upgrade_replace.py \
  tests/test_daemon_stop_all.py \
  tests/test_daemon_status_list.py \
  tests/test_daemon_singleton_integration.py
```
Expected: PASS all.

**Step 3: Run critical baseline gates**

Run:
```bash
uv run pytest -v tests/test_stability.py tests/test_architecture_modern.py tests/test_symbol_algorithms.py
```
Expected: PASS, no regression.

**Step 4: Commit docs and final verification evidence**

```bash
git add README.md README_KR.md docs/TROUBLESHOOTING.md
git commit -m "docs: document strict singleton daemon lifecycle policy"
```

