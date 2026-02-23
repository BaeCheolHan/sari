#!/usr/bin/env python3
"""pipeline perf A/B 반복 실행 및 요약 리포트 생성."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sari.services.pipeline_ab_report import compare_case_metrics, extract_workspace_metrics


def main() -> int:
    args = _parse_args()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    case_a_summaries: list[dict[str, object]] = []
    case_b_summaries: list[dict[str, object]] = []
    case_a_metrics = []
    case_b_metrics = []

    for idx in range(args.repeats):
        case_a = _run_once(
            repo=args.repo,
            target_files=args.target_files,
            profile=args.profile,
            dataset_mode=args.dataset_mode,
            l3_refactored=False,
            run_index=idx,
            case_name="A",
            out_dir=out_dir,
        )
        case_b = _run_once(
            repo=args.repo,
            target_files=args.target_files,
            profile=args.profile,
            dataset_mode=args.dataset_mode,
            l3_refactored=True,
            run_index=idx,
            case_name="B",
            out_dir=out_dir,
        )
        case_a_summaries.append(case_a)
        case_b_summaries.append(case_b)
        case_a_metrics.append(extract_workspace_metrics(case_a))
        case_b_metrics.append(extract_workspace_metrics(case_b))
        print(f"[run {idx + 1}/{args.repeats}] A/B completed")

    comparison = compare_case_metrics(case_a=case_a_metrics, case_b=case_b_metrics)

    payload = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "repo": args.repo,
            "target_files": args.target_files,
            "profile": args.profile,
            "dataset_mode": args.dataset_mode,
            "repeats": args.repeats,
        },
        "comparison": comparison,
        "raw": {
            "case_a": case_a_summaries,
            "case_b": case_b_summaries,
        },
    }
    report_path = out_dir / f"ab_perf_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {report_path}")
    return 0


def _run_once(
    *,
    repo: str,
    target_files: int,
    profile: str,
    dataset_mode: str,
    l3_refactored: bool,
    run_index: int,
    case_name: str,
    out_dir: Path,
) -> dict[str, object]:
    env = dict(os.environ)
    env["SARI_L3_REFACTORED_ORCHESTRATOR_ENABLED"] = "1" if l3_refactored else "0"
    db_path = out_dir / f"ab_case_{case_name.lower()}_{run_index + 1}.db"
    _clean_sqlite_files(db_path)
    env["SARI_DB_PATH"] = str(db_path)
    cmd = [
        "python3",
        "-m",
        "sari.cli.main",
        "pipeline",
        "perf",
        "run",
        "--repo",
        repo,
        "--target-files",
        str(target_files),
        "--profile",
        profile,
        "--dataset-mode",
        dataset_mode,
        "--fresh-db",
        "--reset-probe-state",
        "--cold-lsp-reset",
    ]
    completed = subprocess.run(
        cmd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"perf run failed (l3_refactored={l3_refactored}):\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise RuntimeError(f"invalid perf run output: {completed.stdout}") from exc
    perf = payload.get("perf")
    if not isinstance(perf, dict):
        raise RuntimeError(f"missing perf payload: {payload}")
    return perf


def _clean_sqlite_files(db_path: Path) -> None:
    for path in (
        db_path,
        db_path.with_name(f"{db_path.name}-shm"),
        db_path.with_name(f"{db_path.name}-wal"),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            ...


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pipeline perf A/B benchmark")
    parser.add_argument("--repo", required=True, help="repository root path")
    parser.add_argument("--target-files", type=int, default=300, help="pipeline perf target files")
    parser.add_argument("--profile", default="real_lsp_phase1_v1", help="pipeline perf profile")
    parser.add_argument("--dataset-mode", default="isolated", choices=["isolated", "legacy"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out-dir", default="artifacts/perf")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
