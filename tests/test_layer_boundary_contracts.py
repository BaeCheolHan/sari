from __future__ import annotations

import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE_ROOT = _REPO_ROOT / "src" / "sari" / "core"
_MCP_ROOT = _REPO_ROOT / "src" / "sari" / "mcp"

_ALLOWED_CORE_TO_MCP_IMPORTS: set[tuple[str, str]] = set()


def _iter_core_to_mcp_imports(
    core_root: Path = _CORE_ROOT,
    repo_root: Path = _REPO_ROOT,
) -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []

    for py_file in sorted(core_root.rglob("*.py")):
        try:
            rel_path = py_file.relative_to(repo_root).as_posix()
        except ValueError:
            rel_path = py_file.as_posix()
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    if module == "sari.mcp" or module.startswith("sari.mcp."):
                        violations.append((rel_path, node.lineno, module))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "sari.mcp" or module.startswith("sari.mcp."):
                    violations.append((rel_path, node.lineno, module))

    return violations


def test_core_layer_does_not_import_mcp_modules() -> None:
    violations = _iter_core_to_mcp_imports()

    unexpected = [
        (path, line, module)
        for path, line, module in violations
        if (path, module) not in _ALLOWED_CORE_TO_MCP_IMPORTS
    ]

    assert not unexpected, (
        "Core layer imported MCP modules outside explicit allowlist.\n"
        "Unexpected imports:\n"
        + "\n".join(f"- {path}:{line} imports {module}" for path, line, module in unexpected)
    )


def test_core_layer_boundary_scanner_health_is_debt_neutral(tmp_path: Path) -> None:
    core_root = tmp_path / "src" / "sari" / "core"
    core_root.mkdir(parents=True)
    test_file = core_root / "sample.py"
    test_file.write_text("from sari.mcp.tools.protocol import ErrorCode\n", encoding="utf-8")

    violations = _iter_core_to_mcp_imports(core_root=core_root, repo_root=tmp_path)
    assert ("src/sari/core/sample.py", 1, "sari.mcp.tools.protocol") in violations


def test_mcp_layer_does_not_parse_registry_instances_schema_directly() -> None:
    offenders: list[str] = []
    allowed = {"src/sari/mcp/cli/registry.py"}

    for py_file in sorted(_MCP_ROOT.rglob("*.py")):
        rel_path = py_file.relative_to(_REPO_ROOT).as_posix()
        if rel_path in allowed:
            continue
        source = py_file.read_text(encoding="utf-8")
        if '.get("instances"' in source or ".get('instances'" in source:
            offenders.append(rel_path)

    assert not offenders, (
        "Direct registry schema parsing (.get('instances')) is restricted to CLI registry adapter.\n"
        "Unexpected files:\n"
        + "\n".join(f"- {path}" for path in offenders)
    )
