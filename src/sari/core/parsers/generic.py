import re
import json
from typing import List, Tuple, Dict, Any, Optional
from pathlib import Path
from .base import BaseParser
from .common import _qualname, _symbol_id, _safe_compile, NORMALIZE_KIND_BY_EXT

class GenericRegexParser(BaseParser):
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

    def extract(self, path: str, content: str) -> Tuple[List[Tuple], List[Tuple]]:
        symbols, relations = [], []
        lines = content.splitlines()
        active_scopes = []
        cur_bal = 0
        
        for i, line in enumerate(lines):
            line_no = i + 1
            clean = self.sanitize(line)
            if not clean.strip(): continue

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
                info = {"sid": sid, "path": path, "name": name, "kind": kind, "line": line_no, "meta": "{}", "raw": line.strip(), "qual": name}
                active_scopes.append((cur_bal, info))

            op, cl = clean.count("{"), clean.count("}")
            cur_bal += (op - cl)

            still_active = []
            for bal, info in active_scopes:
                if cur_bal <= bal:
                    # Format B: (path, name, kind, start, end, content, parent, meta, doc, qual, sid)
                    symbols.append((info["path"], info["name"], info["kind"], info["line"], line_no, info["raw"], "", info["meta"], "", info["qual"], info["sid"]))
                else: still_active.append((bal, info))
            active_scopes = still_active

        for _, info in active_scopes:
            symbols.append((info["path"], info["name"], info["kind"], info["line"], len(lines), info["raw"], "", info["meta"], "", info["qual"], info["sid"]))

        if self.ext == ".vue":
            stem = Path(path).stem
            symbols.append((path, stem, "class", 1, len(lines), stem, "", "{}", "", stem, _symbol_id(path, "class", stem)))

        return symbols, relations
