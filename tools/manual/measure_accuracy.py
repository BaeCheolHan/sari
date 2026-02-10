import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from sari.core.parsers.ast_engine import ASTEngine
from sari.core.parsers.factory import ParserFactory


class AccuracyEvaluator:
    def __init__(self):
        self.ast_engine = ASTEngine()

    def evaluate_parser_gap(self, file_path: str) -> Dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            return {}
        content = path.read_text(errors="ignore")
        ext = path.suffix
        regex_parser = ParserFactory.get_parser(ext)
        if not regex_parser:
            return {}
        regex_symbols, _ = regex_parser.extract(str(path), content)

        if not self.ast_engine.enabled:
            return {
                "path": str(path),
                "ts_count": 0,
                "regex_count": len(regex_symbols),
                "overlap_pct": 0.0,
                "ts_disabled": True,
            }

        language = ParserFactory.get_language(ext)
        ts_symbols, _ = self.ast_engine.extract_symbols(str(path), language, content)

        regex_names = {sym.name for sym in regex_symbols}
        ts_names = {sym.name for sym in ts_symbols}
        intersection = regex_names.intersection(ts_names)
        overlap_score = len(intersection) / len(ts_names) if ts_names else 1.0

        return {
            "path": str(path),
            "ts_count": len(ts_symbols),
            "regex_count": len(regex_symbols),
            "overlap_pct": round(overlap_score * 100, 2),
            "false_positives": sorted(list(regex_names - ts_names)),
            "false_negatives": sorted(list(ts_names - regex_names)),
            "ts_disabled": False,
        }


def run_accuracy_report(workspace: str, limit: int) -> Dict[str, Any]:
    evaluator = AccuracyEvaluator()
    root = Path(workspace)
    files = list(root.rglob("*.py")) + list(root.rglob("*.js"))
    files = [f for f in files if "node_modules" not in str(f) and ".venv" not in str(f)][:limit]

    reports: List[Dict[str, Any]] = []
    total_overlap = 0.0
    overlap_count = 0
    ts_active = False
    for file_path in files:
        result = evaluator.evaluate_parser_gap(str(file_path))
        if not result:
            continue
        reports.append(result)
        if not result.get("ts_disabled"):
            ts_active = True
            total_overlap += float(result["overlap_pct"])
            overlap_count += 1

    summary = {
        "workspace": str(root.resolve()),
        "sampled_files": len(reports),
        "ts_active": ts_active,
        "mean_overlap_pct": round(total_overlap / overlap_count, 2) if overlap_count else None,
    }
    status = "ok" if summary["sampled_files"] > 0 else "warn"
    return {
        "status": status,
        "summary": summary,
        "details": {"reports": reports},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure parser overlap between regex and tree-sitter.")
    parser.add_argument("--workspace", default="src/sari/core", help="Target directory to scan.")
    parser.add_argument("--limit", type=int, default=20, help="Max number of files to sample.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_accuracy_report(args.workspace, max(1, int(args.limit)))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        summary = result["summary"]
        print(f"[Accuracy] status={result['status']} workspace={summary['workspace']}")
        print(f"[Accuracy] sampled_files={summary['sampled_files']} ts_active={summary['ts_active']}")
        print(f"[Accuracy] mean_overlap_pct={summary['mean_overlap_pct']}")
        for item in result["details"]["reports"]:
            if item.get("ts_disabled"):
                print(f"- {item['path']}: regex={item['regex_count']} (tree-sitter disabled)")
            else:
                print(
                    f"- {item['path']}: overlap={item['overlap_pct']}% "
                    f"(ts={item['ts_count']}, regex={item['regex_count']})"
                )
    return 0 if result["status"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
