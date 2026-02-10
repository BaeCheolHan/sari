import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, Any

from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.indexer.main import Indexer
from sari.core.workspace import WorkspaceManager


class patch_settings:
    def __init__(self, overrides):
        self.overrides = overrides

    def __enter__(self):
        for key, value in self.overrides.items():
            os.environ[f"SARI__{key}"] = str(value)
            os.environ[f"SARI_{key}"] = str(value)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for key in self.overrides:
            os.environ.pop(f"SARI_{key}", None)
            os.environ.pop(f"SARI__{key}", None)


def run_benchmark(workspace: str, limit: int) -> Dict[str, Any]:
    test_dir = Path(workspace).resolve()
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True)

    for i in range(limit):
        content = f"def func_{i}():\n    return {i}\n" * 10
        (test_dir / f"file_{i}.py").write_text(content, encoding="utf-8")

    db_path = test_dir / "bench.db"
    db = LocalSearchDB(str(db_path))
    norm_path = str(test_dir)
    root_id = WorkspaceManager.root_id(norm_path)
    db.upsert_root(root_id, norm_path, norm_path, label="bench")

    defaults = Config.get_defaults(norm_path)
    defaults["workspace_roots"] = [norm_path]
    cfg = Config(**defaults)

    result: Dict[str, Any] = {
        "workspace": norm_path,
        "root_id": root_id,
        "files_generated": limit,
        "cold_seconds": 0.0,
        "warm_seconds": 0.0,
        "skipped_unchanged": 0,
        "db_file_count": 0,
    }

    with patch_settings({"ENABLE_FTS": "1", "INDEX_L1_BATCH_SIZE": "500", "INDEX_WORKERS": "4"}):
        indexer = Indexer(cfg, db)

        start_cold = time.time()
        indexer.scan_once()
        while indexer.status.indexed_files < limit and (time.time() - start_cold) < 60:
            time.sleep(0.1)
        writer = getattr(getattr(indexer, "storage", None), "writer", None)
        if writer and hasattr(writer, "flush"):
            writer.flush(timeout=5.0)
        result["cold_seconds"] = round(time.time() - start_cold, 3)

        for i in range(limit):
            os.utime(test_dir / f"file_{i}.py", None)

        start_warm = time.time()
        indexer.scan_once()
        while (
            indexer.status.indexed_files + int(getattr(indexer.status, "skipped_unchanged", 0))
        ) < (limit * 2) and (time.time() - start_warm) < 30:
            time.sleep(0.1)
        writer = getattr(getattr(indexer, "storage", None), "writer", None)
        if writer and hasattr(writer, "flush"):
            writer.flush(timeout=2.0)
        result["warm_seconds"] = round(time.time() - start_warm, 3)
        result["skipped_unchanged"] = int(getattr(indexer.status, "skipped_unchanged", 0))

        row = db._read.execute("SELECT COUNT(*) FROM files").fetchone()
        result["db_file_count"] = int(next(iter(row))) if row else 0

    file_ok = result["db_file_count"] >= result["files_generated"]
    status = "ok" if file_ok else "fail"
    return {
        "status": status,
        "summary": {
            "workspace": result["workspace"],
            "files_generated": result["files_generated"],
            "db_file_count": result["db_file_count"],
            "cold_seconds": result["cold_seconds"],
            "warm_seconds": result["warm_seconds"],
        },
        "details": result,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local indexing benchmark.")
    parser.add_argument("--workspace", default="/tmp/sari_benchmark", help="Temporary benchmark workspace path.")
    parser.add_argument("--limit", type=int, default=1000, help="Number of generated files.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_benchmark(args.workspace, max(1, int(args.limit)))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        summary = result["summary"]
        details = result["details"]
        print(f"[Benchmark] status={result['status']} workspace={summary['workspace']}")
        print(f"[Benchmark] files={summary['files_generated']} db_files={summary['db_file_count']}")
        print(f"[Benchmark] cold={summary['cold_seconds']}s warm={summary['warm_seconds']}s skipped={details['skipped_unchanged']}")
    return 0 if result["status"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
