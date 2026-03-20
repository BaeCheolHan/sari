"""Python Protocol 구현체 추론 유틸리티."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from sari.core.models import SymbolSearchItemDTO
from sari.mcp.tools.tool_common import content_hash


@dataclass(frozen=True)
class _MethodSpec:
    name: str
    positional_names: tuple[str, ...]
    kwonly_names: tuple[str, ...]


@dataclass(frozen=True)
class _ClassSpec:
    name: str
    relative_path: str
    line: int
    end_line: int
    content_hash: str
    is_protocol: bool
    base_names: tuple[str, ...]
    methods: dict[str, _MethodSpec]


def scan_python_protocol_implementations(repo_root: str, symbol_name: str, limit: int) -> list[SymbolSearchItemDTO]:
    root = Path(repo_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return []
    class_specs = _collect_class_specs(root)
    protocol_specs = [
        spec
        for spec in class_specs
        if spec.is_protocol and _is_supported_protocol_file(spec.relative_path) and spec.name == symbol_name
    ]
    if len(protocol_specs) == 0:
        return []
    results: list[SymbolSearchItemDTO] = []
    seen: set[tuple[str, int, str]] = set()
    for protocol_spec in protocol_specs:
        for item in _collect_direct_matches(class_specs, protocol_spec, str(root)):
            key = (item.relative_path, item.line, item.name)
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
            if len(results) >= limit:
                return results
        for item in _collect_structural_matches(class_specs, protocol_spec, str(root)):
            key = (item.relative_path, item.line, item.name)
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
            if len(results) >= limit:
                return results
    return results


def _collect_class_specs(root: Path) -> list[_ClassSpec]:
    results: list[_ClassSpec] = []
    for path in root.rglob("*.py"):
        if not path.is_file():
            continue
        relative_path = str(path.relative_to(root)).replace("\\", "/")
        if _is_test_path(relative_path) or is_excluded_candidate_path(relative_path):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        file_hash = content_hash(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            line = int(getattr(node, "lineno", 1))
            end_line = int(getattr(node, "end_lineno", line))
            base_names = tuple(filter(None, (_dotted_name(base) for base in node.bases)))
            results.append(
                _ClassSpec(
                    name=node.name,
                    relative_path=relative_path,
                    line=line,
                    end_line=end_line,
                    content_hash=file_hash,
                    is_protocol=any(base_name.rsplit(".", 1)[-1] == "Protocol" for base_name in base_names),
                    base_names=base_names,
                    methods=_collect_methods(node),
                )
            )
    return results


def _collect_methods(node: ast.ClassDef) -> dict[str, _MethodSpec]:
    methods: dict[str, _MethodSpec] = {}
    for item in node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name.startswith("_"):
            continue
        methods[item.name] = _MethodSpec(
            name=item.name,
            positional_names=tuple(arg.arg for arg in item.args.args[1:]),
            kwonly_names=tuple(arg.arg for arg in item.args.kwonlyargs),
        )
    return methods


def _collect_direct_matches(
    class_specs: list[_ClassSpec],
    protocol_spec: _ClassSpec,
    repo_root: str,
) -> list[SymbolSearchItemDTO]:
    results: list[SymbolSearchItemDTO] = []
    for spec in class_specs:
        if spec.is_protocol:
            continue
        if any(base_name.rsplit(".", 1)[-1] == protocol_spec.name for base_name in spec.base_names):
            results.append(_to_item(spec, repo_root, evidence_type="python_protocol_base", confidence=0.9))
    return sorted(results, key=lambda item: (item.relative_path, item.line, item.name))


def _collect_structural_matches(
    class_specs: list[_ClassSpec],
    protocol_spec: _ClassSpec,
    repo_root: str,
) -> list[SymbolSearchItemDTO]:
    if len(protocol_spec.methods) == 0:
        return []
    results: list[SymbolSearchItemDTO] = []
    required_method_names = set(protocol_spec.methods)
    for spec in class_specs:
        if spec.is_protocol:
            continue
        if any(base_name.rsplit(".", 1)[-1] == protocol_spec.name for base_name in spec.base_names):
            continue
        if not required_method_names.issubset(spec.methods):
            continue
        if not all(_method_matches(protocol_spec.methods[name], spec.methods[name]) for name in required_method_names):
            continue
        results.append(_to_item(spec, repo_root, evidence_type="python_structural_protocol_match", confidence=0.8))
    return sorted(results, key=lambda item: (item.relative_path, item.line, item.name))


def _method_matches(expected: _MethodSpec, candidate: _MethodSpec) -> bool:
    return (
        expected.positional_names == candidate.positional_names
        and expected.kwonly_names == candidate.kwonly_names
    )


def _to_item(spec: _ClassSpec, repo_root: str, *, evidence_type: str, confidence: float) -> SymbolSearchItemDTO:
    return SymbolSearchItemDTO(
        repo=repo_root,
        relative_path=spec.relative_path,
        name=spec.name,
        kind="Class",
        line=spec.line,
        end_line=spec.end_line,
        content_hash=spec.content_hash,
        symbol_key=f"{spec.relative_path}::{spec.name}@{spec.line}",
        confidence=confidence,
        evidence_type=evidence_type,
    )


def _is_supported_protocol_file(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    filename = normalized.rsplit("/", 1)[-1]
    return filename == "ports.py" or filename.endswith("_ports.py")


def _is_test_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    filename = normalized.rsplit("/", 1)[-1]
    return "tests" in parts or filename.startswith("test_") or filename.endswith("_test.py")


def is_excluded_candidate_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    if "site-packages" in parts:
        return True
    if "build" in parts or "dist" in parts:
        return True
    return any(part.startswith(".venv") or part == "venv" for part in parts)


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return node.attr if prefix == "" else f"{prefix}.{node.attr}"
    if isinstance(node, ast.Subscript):
        return _dotted_name(node.value)
    return ""
