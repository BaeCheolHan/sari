#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    module: str


BANNED_EXACT_MODULES: tuple[str, ...] = (
    "sari.core.language_registry",
    "sari.core.lsp_provision_policy",
    "sari.core.repo_context_resolver",
    "sari.core.repo_identity",
    "sari.core.repo_resolver",
    "sari.services.collection.l2_job_processor",
    "sari.services.collection.event_watcher",
    "sari.services.collection.scanner",
    "sari.services.collection.watcher_hotness_tracker",
    "sari.services.collection.solid_lsp_extraction_backend",
    "sari.services.collection.solid_lsp_probe_mixin",
    "sari.services.collection.lsp",
    "sari.services.collection.l3_stages",
    "sari.services.collection.l3_language_processors",
)

BANNED_PREFIX_MODULES: tuple[str, ...] = (
    "sari.services.collection.l3_",
    "sari.services.collection.l4_",
    "sari.services.collection.l5_",
    "sari.services.collection.lsp_",
)


def _is_python_file(path: Path) -> bool:
    return path.is_file() and path.suffix == ".py"


def _iter_py_files(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for root in paths:
        root = root.resolve()
        if not root.exists():
            continue
        if root.is_file():
            if _is_python_file(root):
                result.append(root)
            continue
        result.extend(sorted(p for p in root.rglob("*.py") if p.is_file()))
    return result


def _scan_file(path: Path, project_root: Path) -> list[Violation]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        module = ast.parse(source, filename=str(path))
    except SyntaxError:
        # lint 단계에서 이미 잡히므로 여기서는 skip
        return []

    violations: list[Violation] = []
    rel = str(path.relative_to(project_root))
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in BANNED_EXACT_MODULES:
                violations.append(Violation(file=rel, line=int(getattr(node, "lineno", 0)), module=mod))
                continue
            for prefix in BANNED_PREFIX_MODULES:
                if mod.startswith(prefix):
                    violations.append(Violation(file=rel, line=int(getattr(node, "lineno", 0)), module=mod))
                    break
        elif isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                if mod in BANNED_EXACT_MODULES:
                    violations.append(Violation(file=rel, line=int(getattr(node, "lineno", 0)), module=mod))
                    continue
                for prefix in BANNED_PREFIX_MODULES:
                    if mod.startswith(prefix):
                        violations.append(Violation(file=rel, line=int(getattr(node, "lineno", 0)), module=mod))
                        break
    return violations


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    args = [Path(a).resolve() for a in sys.argv[1:]]
    scan_roots = args if args else [project_root / "src", project_root / "tests"]

    violations: list[Violation] = []
    for file_path in _iter_py_files(scan_roots):
        violations.extend(_scan_file(file_path, project_root=project_root))

    if not violations:
        print(json.dumps({"ok": True, "violations": 0}, ensure_ascii=False))
        return 0

    payload = {
        "ok": False,
        "violations": len(violations),
        "items": [{"file": v.file, "line": v.line, "module": v.module} for v in violations],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
