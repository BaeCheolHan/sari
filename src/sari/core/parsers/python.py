import json
import ast
from typing import List, Tuple, Optional
from .base import BaseParser
from .common import _qualname, _symbol_id, _safe_compile

class PythonParser(BaseParser):
    def extract(self, path: str, content: str) -> Tuple[List[Tuple], List[Tuple]]:
        symbols, relations = [], []
        try:
            tree = ast.parse(content)
            lines = content.splitlines()

            def _visit(node, parent_name="", parent_qual="", current_symbol=None, current_sid=None):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        name = child.name
                        kind = "class" if isinstance(child, ast.ClassDef) else ("method" if parent_name else "function")
                        start, end = child.lineno, getattr(child, "end_lineno", child.lineno)
                        decorators, annos = [], []
                        meta = {}
                        if hasattr(child, "decorator_list"):
                            for dec in child.decorator_list:
                                try:
                                    attr = ""
                                    if isinstance(dec, ast.Name): attr = dec.id
                                    elif isinstance(dec, ast.Attribute): attr = dec.attr
                                    elif isinstance(dec, ast.Call):
                                        if isinstance(dec.func, ast.Name): attr = dec.func.id
                                        elif isinstance(dec.func, ast.Attribute): attr = dec.func.attr
                                        if attr.lower() in ("get", "post", "put", "delete", "patch", "route") and dec.args:
                                            arg = dec.args[0]
                                            val = getattr(arg, "value", getattr(arg, "s", ""))
                                            if isinstance(val, str): meta["http_path"] = val

                                    if attr:
                                        if isinstance(dec, ast.Call):
                                            decorators.append(f"@{attr}(...)")
                                        else:
                                            decorators.append(f"@{attr}")
                                        annos.append(attr.upper())
                                except: pass
                        meta["decorators"] = decorators
                        meta["annotations"] = annos

                        doc = ast.get_docstring(child) or ""
                        if not doc and start > 1:
                            comment_lines = []
                            for j in range(start-2, -1, -1):
                                l = lines[j].strip()
                                if l.endswith("*/"):
                                    for k in range(j, -1, -1):
                                        lk = lines[k].strip()
                                        comment_lines.insert(0, lk)
                                        if lk.startswith("/**") or lk.startswith("/*"): break
                                    break
                            if comment_lines:
                                doc = self.clean_doc(comment_lines)

                        qual = _qualname(parent_qual, name)
                        sid = _symbol_id(path, kind, qual)
                        symbols.append((
                            path,
                            name,
                            kind,
                            start,
                            end,
                            lines[start-1].strip() if 0 <= start-1 < len(lines) else "",
                            parent_name,
                            json.dumps(meta),
                            doc,
                            qual,
                            sid,
                        ))
                        _visit(child, name, qual, name, sid)
                    elif isinstance(child, ast.Call) and current_symbol:
                        target = ""
                        if isinstance(child.func, ast.Name): target = child.func.id
                        elif isinstance(child.func, ast.Attribute): target = child.func.attr
                        if target:
                            relations.append((
                                path,
                                current_symbol,
                                current_sid or "",
                                "",
                                target,
                                "",
                                "calls",
                                child.lineno,
                            ))
                        _visit(child, parent_name, parent_qual, current_symbol, current_sid)
                    else: _visit(child, parent_name, parent_qual, current_symbol, current_sid)
            _visit(tree)
        except Exception:
            from .generic import GenericRegexParser
            config = {
                "re_class": _safe_compile(r"\b(class)\s+([a-zA-Z0-9_]+)"),
                "re_method": _safe_compile(r"\bdef\s+([a-zA-Z0-9_]+)\b\s*\(")
            }
            gen = GenericRegexParser(config, ".py")
            return gen.extract(path, content)
        return symbols, relations