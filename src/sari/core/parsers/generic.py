import re
from typing import List, Dict, Any
from pathlib import Path
from .base import BaseParser
from .common import _symbol_id, _safe_compile, NORMALIZE_KIND_BY_EXT
from sari.core.models import ParserSymbol, ParserRelation, ParseResult


class GenericRegexParser(BaseParser):
    """
    Tree-sitter 라이브러리가 없거나 해당 언어를 지원하지 않을 때 사용하는 범용 정규식 파서입니다.
    미리 정의된 정규식 패턴을 사용하여 클래스와 메서드 구조를 휴리스틱하게 추출합니다.
    """

    def __init__(self, config: Dict[str, Any], ext: str):
        self.ext = ext.lower()
        self.re_class = config["re_class"]
        self.re_method = config["re_method"]
        self.method_kind = config.get("method_kind", "method")
        self.re_anno = _safe_compile(r"^\s*@([a-zA-Z0-9_]+)")
        self.kind_norm = NORMALIZE_KIND_BY_EXT.get(self.ext, {})

    def sanitize(self, line: str) -> str:
        line = re.sub(r"//.*$", "", line)
        line = re.sub(r"#.*$", "", line)
        return line

    def extract(self,
                path: str,
                content: str) -> ParseResult:
        symbols: List[ParserSymbol] = []
        relations: List[ParserRelation] = []
        # Strip block comments first to avoid finding fake symbols in them
        content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
        lines = content.splitlines()
        active_scopes = []
        cur_bal = 0

        for i, line in enumerate(lines):
            line_no = i + 1
            clean = self.sanitize(line)
            if not clean.strip():
                continue

            matches = []
            for m in self.re_class.finditer(clean):
                name = m.group(2) if len(m.groups()) >= 2 else m.group(1)
                kind_raw = m.group(1).lower().strip()
                kind = self.kind_norm.get(kind_raw, "class")
                matches.append((name, kind, m.start()))

            for m in self.re_method.finditer(clean):
                name = next((g for g in m.groups() if g), None)
                if name and not any(name == x[0] for x in matches):
                    matches.append((name, self.method_kind, m.start()))

            for name, kind, _ in matches:
                sid = _symbol_id(path, kind, name)
                info = {
                    "sid": sid,
                    "path": path,
                    "name": name,
                    "kind": kind,
                    "line": line_no,
                    "meta": {},
                    "raw": line.strip(),
                    "qual": name}
                active_scopes.append((cur_bal, info))

            # Safer brace counting
            tmp_line = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', "", clean)
            tmp_line = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "", tmp_line)
            op, cl = tmp_line.count("{"), tmp_line.count("}")
            cur_bal += (op - cl)

            still_active = []
            for bal, info in active_scopes:
                if cur_bal <= bal:
                    symbols.append(
                        ParserSymbol(
                            sid=info["sid"],
                            path=info["path"],
                            name=info["name"],
                            kind=info["kind"],
                            line=info["line"],
                            end_line=line_no,
                            content=info["raw"],
                            meta=info["meta"],
                            qualname=info["qual"]))
                else:
                    still_active.append((bal, info))
            active_scopes = still_active

        for _, info in active_scopes:
            symbols.append(
                ParserSymbol(
                    sid=info["sid"],
                    path=info["path"],
                    name=info["name"],
                    kind=info["kind"],
                    line=info["line"],
                    end_line=len(lines),
                    content=info["raw"],
                    meta=info["meta"],
                    qualname=info["qual"]))

        if self.ext == ".vue":
            stem = Path(path).stem
            symbols.append(
                ParserSymbol(
                    sid=_symbol_id(
                        path,
                        "class",
                        stem),
                    path=path,
                    name=stem,
                    kind="class",
                    line=1,
                    end_line=len(lines),
                    content=stem,
                    qualname=stem))

        return ParseResult(symbols=symbols, relations=relations)


class HCLRegexParser(GenericRegexParser):
    """
    Terraform(HCL) 파일을 처리하기 위한 특화된 정규식 파서입니다.
    resource, module, variable 등 HCL 특유의 블록 구조를 처리합니다.
    """

    def __init__(self, config: Dict[str, Any], ext: str):
        super().__init__(config, ext)
        self.re_resource = _safe_compile(r'^resource\s+"([^"]+)"\s+"([^"]+)"')
        self.re_general = _safe_compile(
            r'^(module|variable|output|data)\s+"([^"]+)"')

    def extract(self,
                path: str,
                content: str) -> ParseResult:
        symbols: List[ParserSymbol] = []
        relations: List[ParserRelation] = []
        content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
        lines = content.splitlines()
        active_scopes = []
        cur_bal = 0

        for i, line in enumerate(lines):
            line_no = i + 1
            clean = self.sanitize(line)
            if not clean.strip():
                continue

            matches = []
            for m in self.re_resource.finditer(clean):
                rtype, rname = m.group(1), m.group(2)
                name = f"{rtype}.{rname}"
                matches.append((name, "resource", m.start()))

            if not matches:
                for m in self.re_general.finditer(clean):
                    btype, bname = m.group(1), m.group(2)
                    matches.append((bname, btype, m.start()))

            for name, kind, _ in matches:
                sid = _symbol_id(path, kind, name)
                info = {
                    "sid": sid,
                    "path": path,
                    "name": name,
                    "kind": kind,
                    "line": line_no,
                    "meta": {},
                    "raw": line.strip(),
                    "qual": name}
                active_scopes.append((cur_bal, info))

            tmp_line = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', "", clean)
            tmp_line = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "", tmp_line)
            op, cl = tmp_line.count("{"), tmp_line.count("}")
            cur_bal += (op - cl)

            still_active = []
            for bal, info in active_scopes:
                if cur_bal <= bal:
                    symbols.append(
                        ParserSymbol(
                            sid=info["sid"],
                            path=info["path"],
                            name=info["name"],
                            kind=info["kind"],
                            line=info["line"],
                            end_line=line_no,
                            content=info["raw"],
                            meta=info["meta"],
                            qualname=info["qual"]))
                else:
                    still_active.append((bal, info))
            active_scopes = still_active

        for _, info in active_scopes:
            symbols.append(
                ParserSymbol(
                    sid=info["sid"],
                    path=info["path"],
                    name=info["name"],
                    kind=info["kind"],
                    line=info["line"],
                    end_line=len(lines),
                    content=info["raw"],
                    meta=info["meta"],
                    qualname=info["qual"]))

        return ParseResult(symbols=symbols, relations=relations)
