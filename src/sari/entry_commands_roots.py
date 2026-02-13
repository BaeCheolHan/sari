import json
import sys
from pathlib import Path

from sari.core.workspace import WorkspaceManager


def _cmd_config_show() -> int:
    cfg_path = WorkspaceManager.resolve_config_path(str(Path.cwd()))
    if not Path(cfg_path).exists():
        print("{}")
        return 0
    print(Path(cfg_path).read_text(encoding="utf-8"))
    return 0


def _cmd_roots_list() -> int:
    cfg_path = WorkspaceManager.resolve_config_path(str(Path.cwd()))
    if not Path(cfg_path).exists():
        print("[]")
        return 0
    data = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    roots = data.get("roots") or data.get("workspace_roots") or []
    print(json.dumps(roots, ensure_ascii=False, indent=2))
    return 0


def _cmd_roots_add(path: str) -> int:
    candidate = WorkspaceManager.normalize_path(path)
    p = Path(candidate).expanduser()
    normalized = WorkspaceManager.normalize_path(str(p))
    if not p.exists() or not p.is_dir():
        print(f"Root path does not exist: {normalized}", file=sys.stderr)
        return 2

    cfg_path = WorkspaceManager.resolve_config_path(str(Path.cwd()))
    data = {}
    if Path(cfg_path).exists():
        try:
            data = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
        except Exception:
            data = {}
    roots = data.get("roots") or data.get("workspace_roots") or []
    roots = [
        WorkspaceManager.normalize_path(str(Path(str(r)).expanduser()))
        for r in roots
        if r
    ]
    roots.append(normalized)
    final = list(dict.fromkeys(roots))
    data["roots"] = final
    Path(cfg_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg_path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


def _cmd_roots_remove(path: str) -> int:
    cfg_path = WorkspaceManager.resolve_config_path(str(Path.cwd()))
    if not Path(cfg_path).exists():
        print("[]")
        return 0
    data = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    roots = data.get("roots") or data.get("workspace_roots") or []
    roots = [r for r in roots if r and r != path]
    data["roots"] = roots
    Path(cfg_path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(roots, ensure_ascii=False, indent=2))
    return 0
