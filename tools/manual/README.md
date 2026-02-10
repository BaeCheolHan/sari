# Manual Validation Scripts

These files are intentionally **not** part of pytest collection.

- `benchmark_performance.py`: ad-hoc performance benchmark
- `measure_accuracy.py`: parser overlap/accuracy report
- `validate_engines.py`: local engine diagnostics
- `verify_indexing_e2e.py`: interactive indexing e2e verification
- `verify_tools_smoke.py`: broad interactive MCP tool smoke runner

Run manually from repository root, for example:

```bash
PYTHONPATH=src python3 tools/manual/validate_engines.py
PYTHONPATH=src python3 tools/manual/verify_tools_smoke.py
```

Common options:

- `--workspace`: target workspace path (or temp workspace when omitted)
- `--limit`: sampling size / file count / retry count (script-specific)
- `--json`: print machine-readable JSON output

JSON schema (common across scripts):

- `status`: `ok` | `warn` | `fail`
- `summary`: compact top-level metrics for quick checks
- `details`: full script-specific payload
