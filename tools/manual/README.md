# Manual Validation Scripts

These files are intentionally **not** part of pytest collection.

- `benchmark_performance.py`: ad-hoc performance benchmark
- `benchmark_ab_indexing.py`: reproducible A/B benchmark for initial indexing
- `measure_accuracy.py`: parser overlap/accuracy report
- `validate_engines.py`: local engine diagnostics
- `verify_indexing_e2e.py`: interactive indexing e2e verification
- `verify_tools_smoke.py`: broad interactive MCP tool smoke runner

Run manually from repository root, for example:

```bash
PYTHONPATH=src python3 tools/manual/validate_engines.py
PYTHONPATH=src python3 tools/manual/verify_tools_smoke.py
uv run python tools/manual/benchmark_ab_indexing.py --workspace /path/to/ws --repeats 5
scripts/benchmark_ab.sh /path/to/ws 5
```

Common options:

- `--workspace`: target workspace path (or temp workspace when omitted)
- `--limit`: sampling size / file count / retry count (script-specific)
- `--json`: print machine-readable JSON output

For `benchmark_ab_indexing.py`:

- outputs `trials.jsonl`, `summary.json`, `report.md` under `<workspace>/.sari-ab-bench` by default
- compare mode A/B via `--mode-a-env KEY=VALUE` / `--mode-b-env KEY=VALUE`
- shell wrapper supports comma-separated env list via:
  - `MODE_A_ENV="K1=V1,K2=V2" scripts/benchmark_ab.sh ...`
  - `MODE_B_ENV="K3=V3" scripts/benchmark_ab.sh ...`

JSON schema (common across scripts):

- `status`: `ok` | `warn` | `fail`
- `summary`: compact top-level metrics for quick checks
- `details`: full script-specific payload
