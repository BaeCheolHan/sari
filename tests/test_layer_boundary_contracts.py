from __future__ import annotations

import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE_ROOT = _REPO_ROOT / "src" / "sari" / "core"

_ALLOWED_CORE_TO_MCP_IMPORTS: set[tuple[str, str]] = {
    ("src/sari/core/async_http_server.py", "sari.mcp.server"),
    ("src/sari/core/async_http_server.py", "sari.mcp.stabilization.warning_sink"),
    ("src/sari/core/daemon_resolver.py", "sari.mcp.stabilization.warning_sink"),
    ("src/sari/core/health.py", "sari.mcp.cli"),
    ("src/sari/core/http_server.py", "sari.mcp.stabilization.warning_sink"),
    ("src/sari/core/http_server.py", "sari.mcp.tools.doctor"),
    ("src/sari/core/http_server.py", "sari.mcp.workspace_registry"),
    ("src/sari/core/main.py", "sari.mcp.server"),
    ("src/sari/core/services/index_service.py", "sari.mcp.tools.protocol"),
}


def _iter_core_to_mcp_imports() -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []

    for py_file in sorted(_CORE_ROOT.rglob("*.py")):
        rel_path = py_file.relative_to(_REPO_ROOT).as_posix()
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


def test_core_layer_boundary_baseline_has_raw_mcp_import_detections() -> None:
    violations = _iter_core_to_mcp_imports()
    assert len(violations) > 0, (
        "Expected baseline core->mcp import detections for evidence. "
        "If this is 0, the scanner is likely broken."
    )
