import re
from typing import List, Tuple, Dict, Optional
from sari.core.models import ParserRelation, ParseResult


class BaseParser:
    """
    Sari 파서의 공통 기능을 정의하는 베이스 클래스입니다.
    문자열 정제, 문서 주석 처리 및 심볼 추출 인터페이스를 제공합니다.
    """

    def sanitize(self, line: str) -> str:
        """
        코드 라인에서 문자열 리터럴과 주석을 제거하여 순수 구조 분석을 용이하게 합니다.
        """
        line = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', line)
        line = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "''", line)
        return line.split('//')[0].strip()

    def clean_doc(self, lines: List[str]) -> str:
        """
        추출된 주석 블록에서 특수 기호(/**, *, */ 등)를 제거하고 깨끗한 텍스트로 변환합니다.
        """
        if not lines:
            return ""
        cleaned = []
        for line_text in lines:
            c = line_text.strip()
            if c.startswith("/**"):
                c = c[3:].strip()
            elif c.startswith("/*"):
                c = c[2:].strip()
            if c.endswith("*/"):
                c = c[:-2].strip()
            while c.startswith("*") or c.startswith(" "):
                c = c[1:]
            if c:
                cleaned.append(c)
            elif cleaned:
                cleaned.append("")
        while cleaned and not cleaned[-1]:
            cleaned.pop()
        return "\n".join(cleaned)

    def extract(self,
                path: str,
                content: str) -> ParseResult:
        """
        소스 코드에서 심볼(클래스, 함수 등)과 관계를 추출합니다.
        자식 클래스에서 반드시 구현해야 합니다.
        """
        raise NotImplementedError


class BaseHandler:
    """
    Tree-sitter 노드 핸들러의 베이스 클래스입니다.
    특정 언어의 AST 노드를 해석하여 Sari 심볼 형식으로 변환하는 역할을 합니다.
    """

    def handle_node(self,
                    node: object,
                    get_t: callable,
                    find_id: callable,
                    ext: str,
                    p_meta: Dict) -> Tuple[Optional[str],
                                           Optional[str],
                                           Dict,
                                           bool]:
        """
        주어진 AST 노드를 분석하여 (심볼종류, 이름, 메타데이터, 유효여부)를 반환합니다.
        """
        return None, None, {}, False

    def handle_relation(
            self,
            node: object,
            context: Dict) -> List[ParserRelation]:
        """
        주어진 AST 노드에서 관계(Relation) 정보를 추출합니다.
        Returns: List[ParserRelation]
        Context: {parent_sid, parent_name, get_t, find_id, ...}
        """
        return []
