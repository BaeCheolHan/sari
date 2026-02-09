import re
import json
import hashlib
from typing import List, Tuple

def _symbol_id(path: str, kind: str, name: str) -> str:
    h = hashlib.sha1(f"{path}:{kind}:{name}".encode()).hexdigest()
    return h

class SpecialParser:
    """
    AST 분석이 어렵거나 구조가 단순한 특수 형식 파일(Dockerfile, MyBatis, Markdown 등)을 위한 전담 파서 집합입니다.
    """
    @staticmethod
    def parse_dockerfile(path: str, content: str) -> List[Tuple]:
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
            meta = json.dumps({"instruction": instr})
            symbols.append((path, instr, "instruction", i + 1, i + 1, raw, "", meta, "", instr, sid))
        return symbols

    @staticmethod
    def parse_mybatis(path: str, content: str) -> List[Tuple]:
        """MyBatis XML 파일에서 SQL 매핑 구문(select, insert 등)의 ID를 심볼로 추출합니다."""
        symbols = []
        for i, line in enumerate(content.splitlines()):
            m = re.search(r'<(select|insert|update|delete|sql)\s+id=["\']([^"\']+)["\']', line)
            if m:
                tag, name = m.group(1), m.group(2)
                sid = _symbol_id(path, "method", name)
                meta = json.dumps({"mybatis_tag": tag, "framework": "MyBatis"})
                symbols.append((path, name, "method", i+1, i+1, line.strip(), "", meta, "", name, sid))
        return symbols

    @staticmethod
    def parse_markdown(path: str, content: str) -> List[Tuple]:
        """Markdown 파일의 헤더(#, ## 등)를 구조적 심볼로 추출합니다."""
        symbols = []
        for i, line in enumerate(content.splitlines()):
            m = re.match(r"^(#+)\s+(.*)", line.strip())
            if m:
                lvl, name = len(m.group(1)), m.group(2)
                sid = _symbol_id(path, "doc", name)
                meta = json.dumps({"lvl": lvl})
                symbols.append((path, name, "doc", i+1, i+1, line.strip(), "", meta, "", name, sid))
        return symbols
