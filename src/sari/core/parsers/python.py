import ast
from typing import List, Tuple
from .base import BaseParser
from .common import _qualname, _symbol_id, _safe_compile
from sari.core.models import ParserSymbol, ParserRelation


class PythonParser(BaseParser):
    """
    Python 표준 라이브러리인 'ast' 모듈을 사용하여 Python 코드를 정밀하게 분석하는 파서입니다.
    클래스, 함수, 메서드 및 데코레이터를 통한 부가 정보(HTTP 엔드포인트 등)를 추출합니다.
    """

    def extract(self,
                path: str,
                content: str) -> Tuple[List[ParserSymbol],
                                       List[ParserRelation]]:
        """
        Python 소스 코드를 파싱하여 심볼 목록과 호출 관계를 추출합니다.
        """
        symbols: List[ParserSymbol] = []
        relations: List[ParserRelation] = []
        try:
            tree = ast.parse(content)
            lines = content.splitlines()

            def _visit(
                    node,
                    parent_name="",
                    parent_qual="",
                    current_symbol_name=None,
                    current_sid=None):
                """AST 노드를 재귀적으로 방문하며 심볼과 관계(Call)를 추출하는 내부 함수입니다."""
                for child in ast.iter_child_nodes(node):
                    if isinstance(
                        child,
                        (ast.FunctionDef,
                         ast.AsyncFunctionDef,
                         ast.ClassDef)):
                        name = child.name
                        kind = "class" if isinstance(
                            child, ast.ClassDef) else (
                            "method" if parent_name else "function")
                        start, end = child.lineno, getattr(
                            child, "end_lineno", child.lineno)
                        decorators, annos = [], []
                        meta = {}
                        if hasattr(child, "decorator_list"):
                            for dec in child.decorator_list:
                                try:
                                    attr = ""
                                    if isinstance(dec, ast.Name):
                                        attr = dec.id
                                    elif isinstance(dec, ast.Attribute):
                                        attr = dec.attr
                                    elif isinstance(dec, ast.Call):
                                        if isinstance(dec.func, ast.Name):
                                            attr = dec.func.id
                                        elif isinstance(dec.func, ast.Attribute):
                                            attr = dec.func.attr
                                        if attr.lower() in (
                                                "get", "post", "put", "delete", "patch", "route") and dec.args:
                                            arg = dec.args[0]
                                            val = getattr(
                                                arg, "value", getattr(arg, "s", ""))
                                            if isinstance(val, str):
                                                meta["http_path"] = val

                                    if attr:
                                        if isinstance(dec, ast.Call):
                                            decorators.append(f"@{attr}(...)")
                                        else:
                                            decorators.append(f"@{attr}")
                                        annos.append(attr.upper())
                                except Exception:
                                    pass
                        meta["decorators"] = decorators
                        meta["annotations"] = annos

                        doc = ast.get_docstring(child) or ""
                        if not doc and start > 1:
                            comment_lines = []
                            for j in range(start - 2, -1, -1):
                                line_text = lines[j].strip()
                                if line_text.endswith("*/"):
                                    for k in range(j, -1, -1):
                                        lk = lines[k].strip()
                                        comment_lines.insert(0, lk)
                                        if lk.startswith(
                                                "/**") or lk.startswith("/*"):
                                            break
                                    break
                            if comment_lines:
                                doc = self.clean_doc(comment_lines)

                        qual = _qualname(parent_qual, name)
                        sid = _symbol_id(path, kind, qual)

                        sym = ParserSymbol(
                            sid=sid, path=path, name=name, kind=kind,
                            line=start, end_line=end, content=lines[start - 1].strip() if 0 <= start - 1 < len(lines) else "",
                            parent=parent_name, meta=meta, doc=doc, qualname=qual
                        )
                        symbols.append(sym)
                        _visit(child, name, qual, name, sid)
                    elif isinstance(child, ast.Call) and current_symbol_name:
                        target = ""
                        if isinstance(child.func, ast.Name):
                            target = child.func.id
                        elif isinstance(child.func, ast.Attribute):
                            target = child.func.attr
                        if target:
                            relations.append(ParserRelation(
                                from_name=current_symbol_name,
                                from_sid=current_sid or "",
                                to_name=target,
                                rel_type="calls",
                                line=child.lineno
                            ))
                        _visit(
                            child,
                            parent_name,
                            parent_qual,
                            current_symbol_name,
                            current_sid)
                    else:
                        _visit(
                            child,
                            parent_name,
                            parent_qual,
                            current_symbol_name,
                            current_sid)
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
