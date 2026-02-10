import re
import hashlib
from typing import List
from sari.core.models import ParserSymbol

def _symbol_id(path: str, kind: str, name: str) -> str:
    h = hashlib.sha1(f"{path}:{kind}:{name}".encode()).hexdigest()
    return h

class SpecialParser:
    """
    AST 분석이 어렵거나 구조가 단순한 특수 형식 파일(Dockerfile, MyBatis, Markdown 등)을 위한 전담 파서 집합입니다.
    """
    @staticmethod
    def parse_dockerfile(path: str, content: str) -> List[ParserSymbol]:
        """Dockerfile 지시어(FROM, RUN 등)를 심볼로 추출합니다."""
        symbols = []
        for i, line in enumerate(content.splitlines()):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            m = re.match(r"^([A-Z]+)\b", raw)
            if not m:
                continue
            instr = m.group(1)
            sid = _symbol_id(path, "instruction", instr)
            symbols.append(ParserSymbol(
                sid=sid, path=path, name=instr, kind="instruction",
                line=i + 1, end_line=i + 1, content=raw,
                meta={"instruction": instr}, qualname=instr
            ))
        return symbols

    @staticmethod
    def parse_mybatis(path: str, content: str) -> List[ParserSymbol]:
        """MyBatis XML 파일에서 SQL 매핑 구문(select, insert 등)의 ID를 심볼로 추출합니다."""
        symbols = []
        for i, line in enumerate(content.splitlines()):
            m = re.search(r'<(select|insert|update|delete|sql)\s+id=["\']([^"\']+)["\']', line)
            if m:
                tag, name = m.group(1), m.group(2)
                sid = _symbol_id(path, "method", name)
                symbols.append(ParserSymbol(
                    sid=sid, path=path, name=name, kind="method",
                    line=i + 1, end_line=i + 1, content=line.strip(),
                    meta={"mybatis_tag": tag, "framework": "MyBatis"}, qualname=name
                ))
        return symbols

    @staticmethod
    def parse_markdown(path: str, content: str) -> List[ParserSymbol]:
        """Markdown 파일의 헤더(#, ## 등)를 구조적 심볼로 추출합니다."""
        symbols = []
        for i, line in enumerate(content.splitlines()):
            m = re.match(r"^(#+)\s+(.*)", line.strip())
            if m:
                lvl, name = len(m.group(1)), m.group(2)
                sid = _symbol_id(path, "doc", name)
                symbols.append(ParserSymbol(
                    sid=sid, path=path, name=name, kind="doc",
                    line=i + 1, end_line=i + 1, content=line.strip(),
                    meta={"lvl": lvl}, qualname=name
                ))
        return symbols
