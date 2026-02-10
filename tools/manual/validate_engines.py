import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

from sari.core.parsers.ast_engine import ASTEngine
from sari.core.parsers.factory import ParserFactory
from sari.core.utils import _normalize_engine_text

PYTHON_SAMPLE = """
class SessionRedirectMixin(object):
    def get_redirect_target(self, resp):
        pass
    def resolve_redirects(self, resp, req):
        pass

class Session(SessionRedirectMixin):
    def __init__(self):
        self.headers = {}
    def request(self, method, url):
        pass
"""

KOREAN_SAMPLE = """
# 네이버 egjs-grid
이 라이브러리는 레이아웃을 효율적으로 배치합니다.
- MasonryGrid: 같은 너비의 아이템을 쌓습니다.
"""


def run_validation(workspace: str, limit: int) -> Dict[str, Any]:
    parser = ParserFactory.get_parser(".py")
    symbols, _relations = parser.extract("sessions.py", PYTHON_SAMPLE)
    symbol_names = [symbol.name for symbol in symbols]
    normalized = _normalize_engine_text(KOREAN_SAMPLE)

    ast_engine = ASTEngine()
    top_symbols = symbol_names[:limit]

    temp_root = Path(workspace) if workspace else Path(tempfile.mkdtemp(prefix="sari_validate_"))
    created_temp = not bool(workspace)
    try:
        if created_temp and temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)
        (temp_root / "file1.txt").write_text("parent", encoding="utf-8")
        child = temp_root / "child"
        child.mkdir(exist_ok=True)
        (child / ".sari").mkdir(exist_ok=True)
        (child / "file2.txt").write_text("child", encoding="utf-8")

        from sari.core.indexer.scanner import Scanner
        from sari.core.settings import settings as global_settings

        cfg = MagicMock()
        cfg.exclude_dirs = []
        cfg.settings = global_settings
        scanner = Scanner(cfg)
        entries = list(scanner.iter_file_entries(temp_root))
        scanned_paths = [str(path.relative_to(temp_root)) for path, _st, _excluded in entries]
    finally:
        if created_temp and temp_root.exists():
            shutil.rmtree(temp_root)

    details = {
        "ast_enabled": bool(ast_engine.enabled),
        "python_symbol_count": len(symbols),
        "python_symbols": top_symbols,
        "cjk_contains_korean": ("네이버" in normalized and "레이아웃" in normalized),
        "scanner_entries": len(scanned_paths),
        "scanner_paths": scanned_paths[:limit],
    }
    status = "ok" if details["python_symbol_count"] > 0 and details["cjk_contains_korean"] else "fail"
    return {
        "status": status,
        "summary": {
            "ast_enabled": details["ast_enabled"],
            "python_symbol_count": details["python_symbol_count"],
            "cjk_contains_korean": details["cjk_contains_korean"],
            "scanner_entries": details["scanner_entries"],
        },
        "details": details,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate core parser/tokenizer/scanner engines.")
    parser.add_argument("--workspace", default="", help="Optional temp workspace path for scanner validation.")
    parser.add_argument("--limit", type=int, default=5, help="Max number of symbols/paths to print.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_validation(args.workspace, max(1, int(args.limit)))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        summary = result["summary"]
        details = result["details"]
        print(f"[Validate] status={result['status']} ast_enabled={summary['ast_enabled']}")
        print(f"[Validate] python_symbol_count={summary['python_symbol_count']} symbols={details['python_symbols']}")
        print(f"[Validate] cjk_contains_korean={summary['cjk_contains_korean']}")
        print(f"[Validate] scanner_entries={summary['scanner_entries']} paths={details['scanner_paths']}")
    return 0 if result["status"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
