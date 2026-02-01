#!/usr/bin/env python3
"""
Deckard Automated Installer
- Clones Deckard to ~/.local/share/horadric-deckard
- Configures Claude Desktop automatically
"""
import os
import sys
import json
import shutil
import subprocess
import signal
from pathlib import Path

REPO_URL = "https://github.com/BaeCheolHan/horadric-deckard.git"
INSTALL_DIR = Path.home() / ".local" / "share" / "horadric-deckard"
REPO_ROOT = Path(__file__).resolve().parent
CLAUDE_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "Claude"
CLAUDE_CONFIG_FILE = CLAUDE_CONFIG_DIR / "claude_desktop_config.json"

def print_step(msg):
    print(f"\\033[1;34m[Deckard Install]\\033[0m {msg}")

def print_success(msg):
    print(f"\\033[1;32m[SUCCESS]\\033[0m {msg}")

def print_error(msg):
    print(f"\\033[1;31m[ERROR]\\033[0m {msg}")

def _run(cmd, **kwargs):
    return subprocess.run(cmd, **kwargs)

def _list_deckard_pids():
    """Best-effort process scan to find deckard-related daemons."""
    try:
        ps = _run(["ps", "-ax", "-o", "pid=", "-o", "command="], capture_output=True, text=True, check=False)
    except Exception:
        return []
    pids = []
    for line in ps.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_str, cmd = line.split(None, 1)
            pid = int(pid_str)
        except Exception:
            continue
        if "mcp.daemon" in cmd or "horadric-deckard" in cmd or "deckard" in cmd:
            if str(INSTALL_DIR) in cmd or "mcp.daemon" in cmd:
                pids.append(pid)
    return pids

def _terminate_pids(pids):
    if not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    for pid in pids:
        try:
            os.kill(pid, 0)
        except Exception:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

def _inspect_codex_config():
    cfg = Path.home() / ".codex" / "config.toml"
    if not cfg.exists():
        return None
    try:
        text = cfg.read_text(encoding="utf-8")
    except Exception:
        return None
    cmd_line = None
    in_deckard = False
    for line in text.splitlines():
        if line.strip() == "[mcp_servers.deckard]":
            in_deckard = True
            continue
        if in_deckard and line.startswith("[") and line.strip() != "[mcp_servers.deckard]":
            in_deckard = False
        if in_deckard and line.strip().startswith("command"):
            cmd_line = line.strip()
            break
    return cmd_line

def _resolve_workspace_root():
    # Prefer explicit envs
    for key in ("DECKARD_WORKSPACE_ROOT", "LOCAL_SEARCH_WORKSPACE_ROOT"):
        val = os.environ.get(key, "").strip()
        if val:
            if val == "${cwd}":
                return str(Path.cwd())
            p = Path(os.path.expanduser(val))
            if p.exists():
                return str(p.resolve())
    # Search for .codex-root from cwd upward
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".codex-root").exists():
            return str(parent)
    return str(cwd)


def _upsert_deckard_block(cfg_path: Path, command_path: str, workspace_root: str):
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists():
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    new_lines = []
    in_deckard = False
    for line in lines:
        if line.strip() == "[mcp_servers.deckard]":
            in_deckard = True
            continue
        if in_deckard and line.startswith("[") and line.strip() != "[mcp_servers.deckard]":
            in_deckard = False
            new_lines.append(line)
            continue
        if not in_deckard:
            new_lines.append(line)

    deckard_block = [
        "[mcp_servers.deckard]",
        f"command = \"{command_path}\"",
        f"args = [\"--workspace-root\", \"{workspace_root}\"]",
        f"env = {{ DECKARD_WORKSPACE_ROOT = \"{workspace_root}\" }}",
        "startup_timeout_sec = 60",
    ]

    insert_at = 0
    for i, line in enumerate(new_lines):
        if line.startswith("model_reasoning_effort"):
            insert_at = i + 1
            break
    new_lines = new_lines[:insert_at] + deckard_block + new_lines[insert_at:]
    cfg_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _sync_codex_config(command_path: str, workspace_root: str):
    project_cfg = Path(workspace_root) / ".codex" / "config.toml"
    _upsert_deckard_block(project_cfg, command_path, workspace_root)
    # Remove deckard block from global config to avoid mixed commands
    global_cfg = Path.home() / ".codex" / "config.toml"
    if global_cfg.exists():
        lines = global_cfg.read_text(encoding="utf-8").splitlines()
        new_lines = []
        in_deckard = False
        for line in lines:
            if line.strip() == "[mcp_servers.deckard]":
                in_deckard = True
                continue
            if in_deckard and line.startswith("[") and line.strip() != "[mcp_servers.deckard]":
                in_deckard = False
                new_lines.append(line)
                continue
            if not in_deckard:
                new_lines.append(line)
        # Backup once
        backup = global_cfg.with_suffix(".toml.bak")
        if not backup.exists():
            backup.write_text("\n".join(lines) + "\n", encoding="utf-8")
        global_cfg.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
def main():
    print_step("Starting Deckard installation...")

    # 1. Clone Repo (fresh install by default)
    if INSTALL_DIR.exists():
        print_step(f"Directory {INSTALL_DIR} exists. Reinstalling (fresh clone)...")
        try:
            shutil.rmtree(INSTALL_DIR)
        except Exception:
            print_error("Failed to remove existing install directory.")
            sys.exit(1)

    print_step(f"Cloning to {INSTALL_DIR}...")
    try:
        subprocess.run(["git", "clone", REPO_URL, str(INSTALL_DIR)], check=True)
    except subprocess.CalledProcessError:
        print_error("Failed to clone git repo.")
        sys.exit(1)

    # 2. Setup Bootstrap
    bootstrap_script = INSTALL_DIR / "bootstrap.sh"
    if not bootstrap_script.exists():
        print_error("bootstrap.sh not found!")
        sys.exit(1)
    
    os.chmod(bootstrap_script, 0o755)
    print_success("Repository set up successfully.")

    # Write VERSION file before removing .git (for update checks)
    try:
        version = subprocess.check_output(["git", "-C", str(INSTALL_DIR), "describe", "--tags", "--abbrev=0"], text=True).strip()
        if version.startswith("v"):
            version = version[1:]
        (INSTALL_DIR / "VERSION").write_text(version + "\n", encoding="utf-8")
    except Exception:
        pass

    # Remove .git to avoid macOS provenance/permission issues
    git_dir = INSTALL_DIR / ".git"
    if git_dir.exists():
        try:
            shutil.rmtree(git_dir)
            print_step("Removed .git directory (fresh install mode).")
        except Exception:
            print_error("Failed to remove .git directory.")

    # Stop running daemon to ensure update application
    print_step("Stopping any running Deckard daemon...")
    try:
        _run([str(bootstrap_script), "daemon", "stop"],
             stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
             timeout=5)
    except Exception:
        pass

    # Fallback: terminate any lingering deckard daemons
    pids = _list_deckard_pids()
    if pids:
        print_step(f"Found running deckard processes: {pids}. Terminating...")
        _terminate_pids(pids)

    # Inspect codex config to avoid mixed command paths
    # Sync Codex config to install path
    workspace_root = _resolve_workspace_root()
    _sync_codex_config(str(REPO_ROOT / "bootstrap.sh"), workspace_root)

    cmd_line = _inspect_codex_config()
    if cmd_line:
        print_step(f"Detected deckard command in ~/.codex/config.toml: {cmd_line}")
        allowed = {str(bootstrap_script), str(REPO_ROOT / "bootstrap.sh")}
        if not any(a in cmd_line for a in allowed):
            print_error("WARNING: Mixed deckard command detected (repo vs install). This can cause protocol mismatch.")
            print("  Recommendation: set command to the install path shown below:")
            print(f"  Command: {bootstrap_script}")

    # 3. Configure Claude Desktop
    if CLAUDE_CONFIG_DIR.exists():
        print_step("Found Claude Desktop configuration.")
        
        config = {}
        if CLAUDE_CONFIG_FILE.exists():
            try:
                with open(CLAUDE_CONFIG_FILE, "r") as f:
                    config = json.load(f)
            except json.JSONDecodeError:
                print_error("Existing config file is invalid JSON. Skipping auto-config.")
                return

        mcp_servers = config.get("mcpServers", {})
        
        # Inject Deckard config
        mcp_servers["deckard"] = {
            "command": str(bootstrap_script),
            "args": [],
            "env": {}
        }
        
        config["mcpServers"] = mcp_servers
        
        # Backup
        if CLAUDE_CONFIG_FILE.exists():
            shutil.copy(CLAUDE_CONFIG_FILE, str(CLAUDE_CONFIG_FILE) + ".bak")
        
        with open(CLAUDE_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
            
        print_success("Added 'deckard' to claude_desktop_config.json")
    else:
        print_step("Claude Desktop not found. Skipping auto-config.")
        print("Manual Config Required:")
        print(f"  Command: {bootstrap_script}")

    print_success("Installation Complete! Restart Claude Desktop to use Deckard.")

if __name__ == "__main__":
    main()
