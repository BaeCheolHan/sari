import re
from typing import List, Tuple

class BaseParser:
    def sanitize(self, line: str) -> str:
        # Replace string literals with empty ones to simplify parsing
        line = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', line)
        line = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "''", line)
        return line.split('//')[0].strip()

    def clean_doc(self, lines: List[str]) -> str:
        if not lines: return ""
        cleaned = []
        for l in lines:
            c = l.strip()
            if c.startswith("/**"): c = c[3:].strip()
            elif c.startswith("/*"): c = c[2:].strip()
            if c.endswith("*/"): c = c[:-2].strip()
            # Robust Javadoc '*' cleaning
            while c.startswith("*") or c.startswith(" "):
                c = c[1:]
            if c: cleaned.append(c)
            elif cleaned: # Preserve purposeful empty lines in docs if already started
                cleaned.append("")
        # Strip trailing empty lines
        while cleaned and not cleaned[-1]: cleaned.pop()
        return "\n".join(cleaned)

    def extract(self, path: str, content: str) -> Tuple[List[Tuple], List[Tuple]]:
        raise NotImplementedError