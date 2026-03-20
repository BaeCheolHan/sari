"""Python semantic caller edge 추출 유틸리티."""

from __future__ import annotations

import ast

from sari.core.models import CallerEdgeDTO
from sari.mcp.tools.tool_common import content_hash


def classify_python_scope(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/").strip().lower()
    filename = normalized.rsplit("/", 1)[-1]
    parts = [part for part in normalized.split("/") if part != ""]
    if "tests" in parts or "test" in parts:
        return "tests"
    if filename.startswith("test_") or filename.endswith("_test.py") or filename.endswith("_spec.py"):
        return "tests"
    return "production"


def scope_matches(*, path_scope: str, scope: str) -> bool:
    normalized = scope.strip().lower()
    if normalized in {"all", "*"}:
        return True
    if normalized in {"tests", "test"}:
        return path_scope == "tests"
    return path_scope == "production"


def extract_python_semantic_call_edges(
    *,
    repo_root: str,
    relative_path: str,
    content_text: str,
) -> list[CallerEdgeDTO]:
    path_scope = classify_python_scope(relative_path)
    try:
        tree = ast.parse(content_text, filename=relative_path)
    except SyntaxError:
        return []
    file_hash = content_hash(content_text)
    results: list[CallerEdgeDTO] = []
    results.extend(
        _scan_route_registration_edges(
            tree=tree,
            repo_root=repo_root,
            relative_path=relative_path,
            file_hash=file_hash,
            path_scope=path_scope,
        )
    )
    results.extend(
        _scan_route_decorator_edges(
            tree=tree,
            repo_root=repo_root,
            relative_path=relative_path,
            file_hash=file_hash,
            path_scope=path_scope,
        )
    )
    results.extend(
        _scan_mcp_dispatch_edges(
            tree=tree,
            repo_root=repo_root,
            relative_path=relative_path,
            file_hash=file_hash,
            path_scope=path_scope,
        )
    )
    results.extend(
        _scan_registry_dispatch_edges(
            tree=tree,
            repo_root=repo_root,
            relative_path=relative_path,
            file_hash=file_hash,
            path_scope=path_scope,
        )
    )
    results.extend(
        _scan_bound_attribute_call_edges(
            tree=tree,
            repo_root=repo_root,
            relative_path=relative_path,
            file_hash=file_hash,
            path_scope=path_scope,
        )
    )
    deduped: list[CallerEdgeDTO] = []
    seen: set[tuple[str, str, int, str, str]] = set()
    for item in results:
        key = (item.relative_path, item.from_symbol, item.line, item.to_symbol, item.evidence_type or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def extract_python_include_router_edges(
    *,
    repo_root: str,
    sources_by_path: dict[str, str],
    scope: str = "production",
) -> list[CallerEdgeDTO]:
    router_exports = _collect_router_endpoint_exports_from_sources(sources_by_path=sources_by_path, scope=scope)
    if len(router_exports) == 0:
        return []
    results: list[CallerEdgeDTO] = []
    for relative_path, source in sources_by_path.items():
        path_scope = classify_python_scope(relative_path)
        if not scope_matches(path_scope=path_scope, scope=scope):
            continue
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError:
            continue
        import_aliases = _collect_import_aliases(tree)
        for owner_name, fn in _iter_callable_owners(tree):
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                func_name = dotted_name(node.func)
                if not (func_name.endswith(".include_router") or func_name == "include_router"):
                    continue
                if len(node.args) == 0:
                    continue
                router_ref = _resolve_imported_router_ref(node.args[0], import_aliases)
                if router_ref == "":
                    continue
                endpoint_names = router_exports.get(router_ref, ())
                for endpoint_name in endpoint_names:
                    results.append(
                        CallerEdgeDTO(
                            repo=repo_root,
                            relative_path=relative_path,
                            from_symbol=owner_name,
                            to_symbol=endpoint_name,
                            line=int(getattr(node, "lineno", getattr(fn, "lineno", 1))),
                            content_hash=content_hash(source),
                            confidence=0.75,
                            evidence_type="python_include_router",
                            scope=path_scope,
                        )
                    )
    deduped: list[CallerEdgeDTO] = []
    seen: set[tuple[str, str, int, str, str]] = set()
    for item in results:
        key = (item.relative_path, item.from_symbol, item.line, item.to_symbol, item.evidence_type or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def candidate_python_base_names(symbol_name: str) -> tuple[str, ...]:
    normalized = symbol_name.strip()
    if normalized == "":
        return ()
    candidates: list[str] = [normalized]
    if "::" in normalized:
        tail = normalized.rsplit("::", 1)[-1]
        if tail not in candidates:
            candidates.append(tail)
    if "." in normalized:
        tail = normalized.rsplit(".", 1)[-1]
        if tail not in candidates:
            candidates.append(tail)
    return tuple(candidates)


def dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        if prefix == "":
            return node.attr
        return f"{prefix}.{node.attr}"
    if isinstance(node, ast.Subscript):
        return dotted_name(node.value)
    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    return ""


def symbol_matches_target(*, candidate: str, target_names: tuple[str, ...]) -> bool:
    if candidate == "":
        return False
    candidate_tail = candidate.rsplit(".", 1)[-1]
    for target in target_names:
        if candidate == target:
            return True
        if candidate_tail == target.rsplit(".", 1)[-1]:
            return True
    return False


def _iter_callable_owners(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    owners: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owners.append((node.name, node))
            continue
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owners.append((f"{node.name}.{item.name}", item))
    return owners


def _scan_route_registration_edges(
    *,
    tree: ast.AST,
    repo_root: str,
    relative_path: str,
    file_hash: str,
    path_scope: str,
) -> list[CallerEdgeDTO]:
    results: list[CallerEdgeDTO] = []
    for owner_name, fn in _iter_callable_owners(tree):
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            func_name = dotted_name(node.func)
            endpoint_name = ""
            if func_name in {"Route", "routing.Route", "starlette.routing.Route"} or func_name.endswith(".Route"):
                if len(node.args) >= 2:
                    endpoint_name = dotted_name(node.args[1])
                if endpoint_name == "":
                    for keyword in node.keywords:
                        if keyword.arg == "endpoint":
                            endpoint_name = dotted_name(keyword.value)
                            break
            elif func_name.endswith(".add_api_route") or func_name == "add_api_route":
                if len(node.args) >= 2:
                    endpoint_name = dotted_name(node.args[1])
                if endpoint_name == "":
                    for keyword in node.keywords:
                        if keyword.arg == "endpoint":
                            endpoint_name = dotted_name(keyword.value)
                            break
            else:
                continue
            if endpoint_name == "":
                continue
            results.append(
                CallerEdgeDTO(
                    repo=repo_root,
                    relative_path=relative_path,
                    from_symbol=owner_name,
                    to_symbol=endpoint_name.rsplit(".", 1)[-1],
                    line=int(getattr(node, "lineno", getattr(fn, "lineno", 1))),
                    content_hash=file_hash,
                    confidence=0.9,
                    evidence_type="python_route_registration",
                    scope=path_scope,
                )
            )
    return results


def _scan_route_decorator_edges(
    *,
    tree: ast.AST,
    repo_root: str,
    relative_path: str,
    file_hash: str,
    path_scope: str,
) -> list[CallerEdgeDTO]:
    results: list[CallerEdgeDTO] = []
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            decorator_name = ""
            if isinstance(decorator, ast.Call):
                decorator_name = dotted_name(decorator.func)
            else:
                decorator_name = dotted_name(decorator)
            if decorator_name == "":
                continue
            decorator_tail = decorator_name.rsplit(".", 1)[-1]
            if decorator_tail not in {"get", "post", "put", "delete", "patch", "route", "websocket"}:
                continue
            results.append(
                CallerEdgeDTO(
                    repo=repo_root,
                    relative_path=relative_path,
                    from_symbol=decorator_name,
                    to_symbol=node.name,
                    line=int(getattr(node, "lineno", 1)),
                    content_hash=file_hash,
                    confidence=0.8,
                    evidence_type="python_route_decorator",
                    scope=path_scope,
                )
            )
    return results


def _scan_mcp_dispatch_edges(
    *,
    tree: ast.AST,
    repo_root: str,
    relative_path: str,
    file_hash: str,
    path_scope: str,
) -> list[CallerEdgeDTO]:
    results: list[CallerEdgeDTO] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        attr_classes: dict[str, str] = {}
        for item in node.body:
            if not isinstance(item, ast.FunctionDef) or item.name != "__init__":
                continue
            for stmt in item.body:
                if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                    continue
                target = stmt.targets[0]
                if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name) or target.value.id != "self":
                    continue
                class_name = dotted_name(stmt.value.func) if isinstance(stmt.value, ast.Call) else ""
                if class_name != "":
                    attr_classes[target.attr] = class_name.rsplit(".", 1)[-1]
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            local_aliases: dict[str, str] = {}
            for stmt in item.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    local_name = stmt.targets[0].id
                    if isinstance(stmt.value, ast.Attribute) and isinstance(stmt.value.value, ast.Name) and stmt.value.value.id == "self":
                        bound_class = attr_classes.get(stmt.value.attr)
                        if bound_class is not None:
                            local_aliases[local_name] = bound_class
                for call in ast.walk(stmt):
                    if not isinstance(call, ast.Call):
                        continue
                    if not isinstance(call.func, ast.Attribute) or call.func.attr != "call":
                        continue
                    owner_class = ""
                    if isinstance(call.func.value, ast.Name):
                        owner_class = local_aliases.get(call.func.value.id, "")
                    elif isinstance(call.func.value, ast.Attribute):
                        attr = call.func.value
                        if isinstance(attr.value, ast.Name) and attr.value.id == "self":
                            owner_class = attr_classes.get(attr.attr, "")
                    if owner_class == "":
                        continue
                    results.append(
                        CallerEdgeDTO(
                            repo=repo_root,
                            relative_path=relative_path,
                            from_symbol=f"{node.name}.{item.name}",
                            to_symbol=f"{owner_class}.call",
                            line=int(getattr(call, "lineno", getattr(item, "lineno", 1))),
                            content_hash=file_hash,
                            confidence=0.85,
                            evidence_type="python_mcp_dispatch",
                            scope=path_scope,
                        )
                    )
    return results


def _scan_bound_attribute_call_edges(
    *,
    tree: ast.AST,
    repo_root: str,
    relative_path: str,
    file_hash: str,
    path_scope: str,
) -> list[CallerEdgeDTO]:
    results: list[CallerEdgeDTO] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        attr_types = _collect_bound_attribute_types(node)
        if len(attr_types) == 0:
            continue
        for item in node.body:
            if not isinstance(item, ast.FunctionDef) or item.name == "__init__":
                continue
            for call in ast.walk(item):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                    continue
                owner = call.func.value
                if not isinstance(owner, ast.Attribute):
                    continue
                if not isinstance(owner.value, ast.Name) or owner.value.id != "self":
                    continue
                attr_type = attr_types.get(owner.attr, "")
                if attr_type == "":
                    continue
                results.append(
                    CallerEdgeDTO(
                        repo=repo_root,
                        relative_path=relative_path,
                        from_symbol=f"{node.name}.{item.name}",
                        to_symbol=f"{attr_type}.{call.func.attr}",
                        line=int(getattr(call, "lineno", getattr(item, "lineno", 1))),
                        content_hash=file_hash,
                        confidence=0.9,
                        evidence_type="python_bound_attribute_call",
                        scope=path_scope,
                    )
                )
    return results


def _scan_registry_dispatch_edges(
    *,
    tree: ast.AST,
    repo_root: str,
    relative_path: str,
    file_hash: str,
    path_scope: str,
) -> list[CallerEdgeDTO]:
    results: list[CallerEdgeDTO] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        attr_classes = _collect_bound_attribute_types(node)
        registry_targets = _collect_registry_targets(class_node=node, attr_classes=attr_classes)
        if len(registry_targets) == 0:
            continue
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            local_registry_aliases: dict[str, dict[str, str]] = {}
            local_handler_targets: dict[str, str] = {}
            for stmt in item.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    local_name = stmt.targets[0].id
                    registry_name = _self_attr_name(stmt.value)
                    if registry_name != "":
                        registry_values = registry_targets.get(registry_name)
                        if registry_values is not None:
                            local_registry_aliases[local_name] = registry_values
                    else:
                        target_symbol = _resolve_registry_lookup_target(
                            node=stmt.value,
                            registry_targets=registry_targets,
                            local_registry_aliases=local_registry_aliases,
                        )
                        if target_symbol != "":
                            local_handler_targets[local_name] = target_symbol
                for call in ast.walk(stmt):
                    target_symbol = _resolve_registry_call_target(
                        node=call,
                        registry_targets=registry_targets,
                        local_registry_aliases=local_registry_aliases,
                        local_handler_targets=local_handler_targets,
                    )
                    if target_symbol == "":
                        continue
                    results.append(
                        CallerEdgeDTO(
                            repo=repo_root,
                            relative_path=relative_path,
                            from_symbol=f"{node.name}.{item.name}",
                            to_symbol=target_symbol,
                            line=int(getattr(call, "lineno", getattr(item, "lineno", 1))),
                            content_hash=file_hash,
                            confidence=0.8,
                            evidence_type="python_registry_dispatch",
                            scope=path_scope,
                        )
                    )
    return results


def _collect_bound_attribute_types(class_node: ast.ClassDef) -> dict[str, str]:
    attr_types: dict[str, str] = {}
    init_fn: ast.FunctionDef | None = None
    for item in class_node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            init_fn = item
            break
    if init_fn is None:
        return attr_types
    param_types: dict[str, str] = {}
    for arg in init_fn.args.args:
        if arg.arg == "self" or arg.annotation is None:
            continue
        annotation_name = dotted_name(arg.annotation)
        if annotation_name != "":
            param_types[arg.arg] = annotation_name.rsplit(".", 1)[-1]
    for stmt in init_fn.body:
        if isinstance(stmt, ast.Assign):
            value = stmt.value
            for target in stmt.targets:
                _record_bound_attr_type(attr_types=attr_types, param_types=param_types, target=target, value=value)
            continue
        if isinstance(stmt, ast.AnnAssign):
            _record_bound_attr_type(attr_types=attr_types, param_types=param_types, target=stmt.target, value=stmt.value)
    return attr_types


def _record_bound_attr_type(
    *,
    attr_types: dict[str, str],
    param_types: dict[str, str],
    target: ast.expr,
    value: ast.expr | None,
) -> None:
    if not isinstance(target, ast.Attribute):
        return
    if not isinstance(target.value, ast.Name) or target.value.id != "self":
        return
    if value is None:
        return
    if isinstance(value, ast.Name):
        inferred = param_types.get(value.id, "")
        if inferred != "":
            attr_types[target.attr] = inferred
        return
    if isinstance(value, ast.Call):
        ctor_name = dotted_name(value.func)
        if ctor_name != "":
            attr_types[target.attr] = ctor_name.rsplit(".", 1)[-1]


def _collect_registry_targets(*, class_node: ast.ClassDef, attr_classes: dict[str, str]) -> dict[str, dict[str, str]]:
    init_fn: ast.FunctionDef | None = None
    for item in class_node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            init_fn = item
            break
    if init_fn is None:
        return {}
    registry_targets: dict[str, dict[str, str]] = {}
    for stmt in init_fn.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target_name = _self_attr_name(stmt.targets[0])
        if target_name == "" or not isinstance(stmt.value, ast.Dict):
            continue
        values_by_key: dict[str, str] = {}
        for key_node, value_node in zip(stmt.value.keys, stmt.value.values, strict=False):
            if key_node is None:
                continue
            literal_key = _literal_string(key_node)
            if literal_key == "":
                continue
            target_symbol = _resolve_registry_value_target(value_node=value_node, attr_classes=attr_classes)
            if target_symbol != "":
                values_by_key[literal_key] = target_symbol
        if len(values_by_key) > 0:
            registry_targets[target_name] = values_by_key
    return registry_targets


def _resolve_registry_value_target(*, value_node: ast.expr, attr_classes: dict[str, str]) -> str:
    if isinstance(value_node, ast.Attribute):
        owner_name = dotted_name(value_node.value)
        if owner_name.startswith("self."):
            attr_type = attr_classes.get(owner_name.split(".", 1)[1], "")
            if attr_type != "":
                return f"{attr_type}.{value_node.attr}"
        owner_tail = owner_name.rsplit(".", 1)[-1]
        if owner_tail != "":
            return f"{owner_tail}.{value_node.attr}"
    dotted = dotted_name(value_node)
    if dotted != "":
        return dotted.rsplit(".", 1)[-1]
    return ""


def _resolve_registry_lookup_target(
    *,
    node: ast.expr,
    registry_targets: dict[str, dict[str, str]],
    local_registry_aliases: dict[str, dict[str, str]],
) -> str:
    if not isinstance(node, ast.Subscript):
        return ""
    registry_values = _resolve_registry_values(
        node=node.value,
        registry_targets=registry_targets,
        local_registry_aliases=local_registry_aliases,
    )
    target_symbol = _select_registry_target(node=node, registry_values=registry_values)
    if target_symbol == "":
        return ""
    return target_symbol


def _resolve_registry_call_target(
    *,
    node: ast.AST,
    registry_targets: dict[str, dict[str, str]],
    local_registry_aliases: dict[str, dict[str, str]],
    local_handler_targets: dict[str, str],
) -> str:
    if not isinstance(node, ast.Call):
        return ""
    if isinstance(node.func, ast.Name):
        return local_handler_targets.get(node.func.id, "")
    if not isinstance(node.func, ast.Subscript):
        return ""
    registry_values = _resolve_registry_values(
        node=node.func.value,
        registry_targets=registry_targets,
        local_registry_aliases=local_registry_aliases,
    )
    target_symbol = _select_registry_target(node=node.func, registry_values=registry_values)
    if target_symbol == "":
        return ""
    return target_symbol


def _resolve_registry_values(
    *,
    node: ast.expr,
    registry_targets: dict[str, dict[str, str]],
    local_registry_aliases: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    if isinstance(node, ast.Name):
        return local_registry_aliases.get(node.id)
    registry_name = _self_attr_name(node)
    if registry_name == "":
        return None
    return registry_targets.get(registry_name)


def _select_registry_target(*, node: ast.Subscript, registry_values: dict[str, str] | None) -> str:
    if registry_values is None or len(registry_values) == 0:
        return ""
    literal_key = _literal_string(_subscript_key(node))
    if literal_key != "":
        return registry_values.get(literal_key, "")
    if len(registry_values) == 1:
        return next(iter(registry_values.values()))
    return ""


def _self_attr_name(node: ast.expr) -> str:
    if not isinstance(node, ast.Attribute):
        return ""
    if not isinstance(node.value, ast.Name) or node.value.id != "self":
        return ""
    return node.attr


def _literal_string(node: ast.expr) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _subscript_key(node: ast.Subscript) -> ast.expr:
    return node.slice


def _collect_router_endpoint_exports_from_sources(
    *,
    sources_by_path: dict[str, str],
    scope: str,
) -> dict[str, tuple[str, ...]]:
    results: dict[str, tuple[str, ...]] = {}
    for relative_path, source in sources_by_path.items():
        path_scope = classify_python_scope(relative_path)
        if not scope_matches(path_scope=path_scope, scope=scope):
            continue
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError:
            continue
        module_name = relative_path[:-3].replace("/", ".")
        router_names: set[str] = set()
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        router_names.add(target.id)
        exported: dict[str, list[str]] = {}
        for node in getattr(tree, "body", []):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                decorator_name = dotted_name(decorator.func) if isinstance(decorator, ast.Call) else dotted_name(decorator)
                if decorator_name == "" or "." not in decorator_name:
                    continue
                router_name = decorator_name.split(".", 1)[0]
                decorator_tail = decorator_name.rsplit(".", 1)[-1]
                if router_name not in router_names:
                    continue
                if decorator_tail not in {"get", "post", "put", "delete", "patch", "route", "websocket"}:
                    continue
                exported.setdefault(router_name, []).append(node.name)
        for router_name, endpoint_names in exported.items():
            results[f"{module_name}:{router_name}"] = tuple(endpoint_names)
    return results


def _collect_import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        for alias in node.names:
            local_name = alias.asname or alias.name
            aliases[local_name] = f"{node.module}:{alias.name}"
    return aliases


def _resolve_imported_router_ref(node: ast.expr, import_aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return import_aliases.get(node.id, "")
    return ""
