from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, UTC

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "src"))

from sari.services.language_probe.java_lsp_benchmark import (  # noqa: E402
    build_markdown_report,
    observations_to_jsonable,
    run_java_lsp_benchmark,
    summarize_observations,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Java LSP benchmark against a target repo")
    parser.add_argument("--repo-root", required=True, help="Target Java repository root")
    parser.add_argument("--repeats", type=int, default=3, help="Number of repeats per provider")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "artifacts" / "java-lsp-benchmark"),
        help="Directory where JSON/Markdown reports are written",
    )
    parser.add_argument(
        "--javalight-home",
        default=None,
        help="Optional explicit JavaLight dist home. If omitted, env/provider defaults are used.",
    )
    args = parser.parse_args()

    repo_root = str(Path(args.repo_root).expanduser().resolve())
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    observations = run_java_lsp_benchmark(
        repo_root=repo_root,
        repeats=max(1, int(args.repeats)),
        javalight_home=args.javalight_home,
    )
    json_path = output_dir / f"java-lsp-benchmark-{timestamp}.json"
    md_path = output_dir / f"java-lsp-benchmark-{timestamp}.md"
    json_path.write_text(
        json.dumps(
            {
                "repo_root": repo_root,
                "generated_at": timestamp,
                "repeats": max(1, int(args.repeats)),
                "observations": observations_to_jsonable(observations),
                "summary": [item.__dict__ for item in summarize_observations(observations)],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    md_path.write_text(build_markdown_report(repo_root=repo_root, observations=observations), encoding="utf-8")

    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    for item in summarize_observations(observations):
        print(
            f"{item.provider:14} {item.request_kind:15} {item.phase:4} "
            f"runs={item.run_count} success={item.success_rate:.2f} "
            f"median_ms={item.median_latency_ms:.1f} median_count={item.median_result_count}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
