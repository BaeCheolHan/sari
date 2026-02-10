import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

os.environ["SARI_LOG_LEVEL"] = "INFO"
os.environ["PYTHONPATH"] = str(SRC_ROOT)

from sari.core.db.models import File, Symbol
from sari.core.utils.logging import configure_logging
from sari.mcp.workspace_registry import Registry

configure_logging()


def create_dummy_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".sariroot").touch()
    (root / "main.py").write_text(
        "def hello_world():\n"
        "    print('Hello E2E')\n\n"
        "class TestClass:\n"
        "    def method_one(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    (root / "utils").mkdir(exist_ok=True)
    (root / "utils" / "helper.py").write_text("FULL_CONSTANT = 42\n", encoding="utf-8")
    (root / "requirements.txt").touch()


def run_verify_indexing_e2e(workspace: str, limit: int) -> Dict[str, Any]:
    created_temp = not bool(workspace)
    tmp_dir = tempfile.mkdtemp(prefix="sari_e2e_") if created_temp else ""
    root_path = (Path(tmp_dir) / "project") if created_temp else Path(workspace).resolve()
    if not created_temp:
        if root_path.exists():
            shutil.rmtree(root_path)
    create_dummy_project(root_path)

    sari_dir = root_path / ".sari"
    sari_dir.mkdir(parents=True, exist_ok=True)
    (sari_dir / "index.db").touch()

    from sari.core.config import Config
    from sari.core.indexer.scanner import Scanner
    from sari.core.settings import settings as global_settings

    defaults = Config.get_defaults(str(root_path))
    cfg_obj = Config(**defaults)
    cfg_obj.settings = global_settings
    scanner = Scanner(cfg_obj)
    scanner_entries = list(scanner.iter_file_entries(root_path))
    scanned_count = sum(1 for _path, _st, excluded in scanner_entries if not excluded)

    registry = Registry.get_instance()
    state = registry.get_or_create(str(root_path))
    found_count = 0
    for _ in range(max(1, limit)):
        found_count = File.select().where(File.root_id == state.root_id).count()
        if found_count >= 2:
            break
        time.sleep(1)

    symbols = list(Symbol.select().where(Symbol.root_id == state.root_id))
    symbol_names = sorted({symbol.name for symbol in symbols})
    expected = {"hello_world", "TestClass", "method_one"}
    missing = sorted(list(expected - set(symbol_names)))
    success = found_count >= 2 and "hello_world" in symbol_names

    registry.shutdown_all()
    if created_temp and Path(tmp_dir).exists():
        shutil.rmtree(tmp_dir)

    details = {
        "workspace": str(root_path),
        "scanner_valid_files": scanned_count,
        "indexed_files": found_count,
        "symbol_count": len(symbol_names),
        "symbol_names": symbol_names,
        "missing_expected_symbols": missing,
        "success": success,
    }
    status = "ok" if success else "fail"
    return {
        "status": status,
        "summary": {
            "workspace": details["workspace"],
            "scanner_valid_files": details["scanner_valid_files"],
            "indexed_files": details["indexed_files"],
            "symbol_count": details["symbol_count"],
            "success": details["success"],
        },
        "details": details,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify indexing E2E on a generated or provided workspace.")
    parser.add_argument("--workspace", default="", help="Optional workspace path. If omitted, a temp workspace is used.")
    parser.add_argument("--limit", type=int, default=20, help="Max retry attempts while waiting for indexing.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_verify_indexing_e2e(args.workspace, max(1, int(args.limit)))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        summary = result["summary"]
        details = result["details"]
        print(f"[E2E] status={result['status']} workspace={summary['workspace']}")
        print(f"[E2E] scanner_valid_files={summary['scanner_valid_files']} indexed_files={summary['indexed_files']}")
        print(f"[E2E] symbol_count={summary['symbol_count']} missing={details['missing_expected_symbols']}")
        print(f"[E2E] success={summary['success']}")
    return 0 if result["status"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
