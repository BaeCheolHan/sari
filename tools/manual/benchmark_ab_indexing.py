import argparse
import contextlib
import json
import logging
import os
import statistics
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List

from sari.core.config import Config
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import _scan_to_db
from sari.core.workspace import WorkspaceManager


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(float(v) for v in values)
    k = (len(ordered) - 1) * max(0.0, min(100.0, float(p))) / 100.0
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def _safe_median(values: List[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def _improvement_pct(a: float, b: float) -> float:
    # Positive means B is better (smaller metric value than A).
    if a <= 0:
        return 0.0
    return ((a - b) / a) * 100.0


def _parse_env_overrides(items: Iterable[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        if "=" not in text:
            raise ValueError(f"invalid env override: {text} (expected KEY=VALUE)")
        key, value = text.split("=", 1)
        k = key.strip()
        if not k:
            raise ValueError(f"invalid env override key: {text}")
        out[k] = value
    return out


@contextlib.contextmanager
def _patch_env(overrides: Dict[str, str]) -> Iterator[None]:
    old: Dict[str, str | None] = {}
    try:
        for k, v in overrides.items():
            old[k] = os.environ.get(k)
            os.environ[k] = str(v)
        yield
    finally:
        for k, prev in old.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


def _get_rss_kib_current() -> int:
    try:
        import psutil  # type: ignore

        return int(psutil.Process(os.getpid()).memory_info().rss // 1024)
    except Exception:
        pass
    try:
        import resource

        return int(getattr(resource.getrusage(resource.RUSAGE_SELF), "ru_maxrss", 0) or 0)
    except Exception:
        return 0


def _get_maxrss_kib() -> int:
    try:
        import resource

        return int(getattr(resource.getrusage(resource.RUSAGE_SELF), "ru_maxrss", 0) or 0)
    except Exception:
        return 0


def _run_single_trial(
    *,
    mode: str,
    trial_no: int,
    workspace: Path,
    out_dir: Path,
    env_overrides: Dict[str, str],
) -> Dict[str, Any]:
    db_path = out_dir / f"ab_{mode.lower()}_{trial_no}.db"
    if db_path.exists():
        db_path.unlink()

    defaults = Config.get_defaults(str(workspace))
    defaults["workspace_root"] = str(workspace)
    defaults["workspace_roots"] = [str(workspace)]
    defaults["db_path"] = str(db_path)
    cfg = Config(**defaults)

    logger = logging.getLogger("sari.ab_benchmark")
    db = LocalSearchDB(str(db_path), logger=logger)
    root_id = WorkspaceManager.root_id(str(workspace))
    db.upsert_root(root_id, str(workspace), str(workspace), label=workspace.name)

    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    start_rss = _get_rss_kib_current()
    with _patch_env(env_overrides):
        status = _scan_to_db(cfg, db, logger)
    wall_s = time.perf_counter() - start_wall
    cpu_s = time.process_time() - start_cpu
    end_rss = _get_rss_kib_current()

    files = int(
        db.execute("SELECT COUNT(1) FROM files WHERE deleted_ts = 0 AND root_id = ?", (root_id,)).fetchone()[0]
        or 0
    )
    symbols = int(
        db.execute("SELECT COUNT(1) FROM symbols WHERE root_id = ?", (root_id,)).fetchone()[0] or 0
    )
    relations = int(
        db.execute(
            "SELECT COUNT(1) FROM symbol_relations WHERE from_root_id = ? OR to_root_id = ?",
            (root_id, root_id),
        ).fetchone()[0]
        or 0
    )
    db.close_all()

    return {
        "mode": mode,
        "trial": int(trial_no),
        "workspace": str(workspace),
        "wall_s": round(float(wall_s), 6),
        "cpu_s": round(float(cpu_s), 6),
        "maxrss_kib_delta": max(0, int(end_rss - start_rss)),
        "files": files,
        "symbols": symbols,
        "relations": relations,
        "status": {
            "scanned_files": int(status.get("scanned_files", 0) or 0),
            "indexed_files": int(status.get("indexed_files", 0) or 0),
            "symbols_extracted": int(status.get("symbols_extracted", 0) or 0),
            "errors": int(status.get("errors", 0) or 0),
        },
    }


def summarize_trials(trials: List[Dict[str, Any]], *, integrity_scope: str = "full") -> Dict[str, Any]:
    grouped = {"A": [], "B": []}
    for row in trials:
        mode = str(row.get("mode", "")).upper()
        if mode in grouped:
            grouped[mode].append(row)

    def _mode_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        wall = [float(r.get("wall_s", 0.0) or 0.0) for r in rows]
        cpu = [float(r.get("cpu_s", 0.0) or 0.0) for r in rows]
        rss = [float(r.get("maxrss_kib_delta", 0) or 0) for r in rows]
        files = [int(r.get("files", 0) or 0) for r in rows]
        symbols = [int(r.get("symbols", 0) or 0) for r in rows]
        relations = [int(r.get("relations", 0) or 0) for r in rows]
        return {
            "runs": len(rows),
            "wall_s_median": round(_safe_median(wall), 6),
            "wall_s_p95": round(_percentile(wall, 95), 6),
            "cpu_s_median": round(_safe_median(cpu), 6),
            "cpu_s_p95": round(_percentile(cpu, 95), 6),
            "maxrss_kib_median": int(_safe_median(rss)),
            "maxrss_kib_p95": int(_percentile(rss, 95)),
            "files_set": sorted(set(files)),
            "symbols_set": sorted(set(symbols)),
            "relations_set": sorted(set(relations)),
        }

    a = _mode_summary(grouped["A"])
    b = _mode_summary(grouped["B"])
    wall_improve = _improvement_pct(float(a["wall_s_median"]), float(b["wall_s_median"]))
    cpu_improve = _improvement_pct(float(a["cpu_s_median"]), float(b["cpu_s_median"]))

    scope = str(integrity_scope or "full").strip().lower()
    if scope == "files":
        integrity_ok = bool(a["files_set"]) and bool(b["files_set"]) and a["files_set"] == b["files_set"]
    else:
        integrity_ok = (
            bool(a["files_set"])
            and bool(b["files_set"])
            and a["files_set"] == b["files_set"]
            and a["symbols_set"] == b["symbols_set"]
            and a["relations_set"] == b["relations_set"]
        )
    load_guard_ok = (
        float(b["cpu_s_p95"]) <= (float(a["cpu_s_p95"]) * 1.10 if float(a["cpu_s_p95"]) > 0 else float(b["cpu_s_p95"]))
        and float(b["maxrss_kib_p95"])
        <= (float(a["maxrss_kib_p95"]) * 1.10 if float(a["maxrss_kib_p95"]) > 0 else float(b["maxrss_kib_p95"]))
    )

    return {
        "mode_A": a,
        "mode_B": b,
        "improvement_pct": {
            "wall_s_median": round(wall_improve, 3),
            "cpu_s_median": round(cpu_improve, 3),
        },
        "gates": {
            "integrity_ok": bool(integrity_ok),
            "load_guard_ok": bool(load_guard_ok),
        },
        "integrity_scope": scope,
    }


def _render_markdown(summary: Dict[str, Any], *, workspace: Path, repeats: int) -> str:
    imp = summary.get("improvement_pct", {})
    gates = summary.get("gates", {})
    a = summary.get("mode_A", {})
    b = summary.get("mode_B", {})
    return "\n".join(
        [
            "# A/B Initial Indexing Benchmark Report",
            "",
            f"- Workspace: `{workspace}`",
            f"- Repeats per mode: `{int(repeats)}`",
            "",
            "## Improvement",
            f"- Wall median improvement (B vs A): `{imp.get('wall_s_median', 0)}%`",
            f"- CPU median improvement (B vs A): `{imp.get('cpu_s_median', 0)}%`",
            "",
            "## Gates",
            f"- Integrity OK: `{gates.get('integrity_ok', False)}`",
            f"- Load Guard OK: `{gates.get('load_guard_ok', False)}`",
            "",
            "## Mode A",
            f"- wall median/p95: `{a.get('wall_s_median', 0)}` / `{a.get('wall_s_p95', 0)}`",
            f"- cpu median/p95: `{a.get('cpu_s_median', 0)}` / `{a.get('cpu_s_p95', 0)}`",
            f"- rss median/p95 (KiB): `{a.get('maxrss_kib_median', 0)}` / `{a.get('maxrss_kib_p95', 0)}`",
            "",
            "## Mode B",
            f"- wall median/p95: `{b.get('wall_s_median', 0)}` / `{b.get('wall_s_p95', 0)}`",
            f"- cpu median/p95: `{b.get('cpu_s_median', 0)}` / `{b.get('cpu_s_p95', 0)}`",
            f"- rss median/p95 (KiB): `{b.get('maxrss_kib_median', 0)}` / `{b.get('maxrss_kib_p95', 0)}`",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A/B benchmark for initial indexing.")
    p.add_argument("--workspace", required=True, help="Workspace path to benchmark.")
    p.add_argument("--repeats", type=int, default=5, help="Repeats per mode (A and B).")
    p.add_argument("--out-dir", default="", help="Output directory for jsonl/json/md.")
    p.add_argument("--mode-a-env", action="append", default=[], help="Mode A env override (KEY=VALUE).")
    p.add_argument("--mode-b-env", action="append", default=[], help="Mode B env override (KEY=VALUE).")
    p.add_argument("--integrity-scope", default="full", choices=["full", "files"], help="Integrity gate scope.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace).resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise SystemExit(f"workspace not found: {workspace}")

    repeats = max(1, int(args.repeats))
    out_dir = Path(args.out_dir).resolve() if args.out_dir else workspace / ".sari-ab-bench"
    out_dir.mkdir(parents=True, exist_ok=True)

    mode_a_env = _parse_env_overrides(args.mode_a_env)
    mode_b_env = _parse_env_overrides(args.mode_b_env)

    trials: List[Dict[str, Any]] = []
    sequence: List[str] = []
    for _ in range(repeats):
        sequence.extend(["A", "B"])

    for idx, mode in enumerate(sequence, start=1):
        envs = mode_a_env if mode == "A" else mode_b_env
        row = _run_single_trial(
            mode=mode,
            trial_no=idx,
            workspace=workspace,
            out_dir=out_dir,
            env_overrides=envs,
        )
        trials.append(row)

    jsonl_path = out_dir / "trials.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in trials:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = summarize_trials(trials, integrity_scope=str(args.integrity_scope))
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report = _render_markdown(summary, workspace=workspace, repeats=repeats)
    report_path = out_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")

    print(json.dumps({"trials": str(jsonl_path), "summary": str(summary_path), "report": str(report_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
