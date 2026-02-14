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
uv run python tools/manual/benchmark_ab_indexing.py --workspace /path/to/ws1 --workspace /path/to/ws2 --repeats 5
scripts/benchmark_ab.sh /path/to/ws1,/path/to/ws2 5
```

Common options:

- `--workspace`: target workspace path (repeatable for multi-root benchmark)
- `--limit`: sampling size / file count / retry count (script-specific)
- `--json`: print machine-readable JSON output

For `benchmark_ab_indexing.py`:

- outputs `trials.jsonl`, `summary.json`, `report.md` under `<workspace>/.sari-ab-bench` by default
- compare mode A/B via `--mode-a-env KEY=VALUE` / `--mode-b-env KEY=VALUE`
- include B 2-stage completion time with backfill via `--mode-b-backfill-full`
- scanner backend A/B can be compared with env:
- `SARI_SCANNER_BACKEND=python` (default)
- `SARI_SCANNER_BACKEND=rust` (optional sidecar scanner)
- `SARI_INDEXER_INITIAL_FASTPATH=1` (default): skip per-file DB metadata lookup on empty roots
- `SARI_INDEXER_INITIAL_PROCESS_POOL=0` (default, opt-in): use process pool for initial indexing
  - Experimental path: environment/permission dependent, deterministic performance is **not guaranteed**.
  - If process pool is unavailable (restricted env/sandbox), indexer auto-falls back to thread pool.
- `SARI_INDEXER_VALUE_INDEX_SPLIT=0` (opt-in): initial pass stores light metadata/symbol/relation first,
  defers heavy file payload(content/fts) and backfills on next scan.
- `SARI_WAL_IDLE_CHECKPOINT=1` (default): disable auto-checkpoint and run PASSIVE checkpoint only when idle.
- `SARI_INDEXER_COMBINED_SYMBOL_REL_TX=1` (default): flush symbols+relations in one transaction to reduce commit overhead.
- Laptop-friendly defaults (override when needed):
  - `SARI_INDEXER_RESERVE_CORES=2` (leave headroom for UI)
  - `SARI_INDEXER_MAX_WORKERS_CAP=8` (prevent extreme fan/noise spikes)
  - `SARI_INDEXER_MAX_WORKERS` (explicit worker override)
  - `SARI_INDEXER_MAX_INFLIGHT` (explicit inflight queue override)
- shell wrapper supports comma-separated env list via:
  - `MODE_A_ENV="K1=V1,K2=V2" scripts/benchmark_ab.sh ...`
  - `MODE_B_ENV="K3=V3" scripts/benchmark_ab.sh ...`

JSON schema (common across scripts):

- `status`: `ok` | `warn` | `fail`
- `summary`: compact top-level metrics for quick checks
- `details`: full script-specific payload
