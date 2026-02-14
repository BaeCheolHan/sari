# A/B Initial Indexing Benchmark Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a reproducible A/B benchmark harness to compare baseline vs improved initial indexing runtime and system load.

**Architecture:** Introduce a standalone benchmark CLI that runs controlled repeated trials for mode A and mode B against the same workspace snapshot and outputs JSONL + summarized Markdown. Use environment overlays to toggle experimental knobs without changing code paths manually. Keep data integrity checks in both modes.

**Tech Stack:** Python 3.12, existing sari core indexer APIs, uv, JSON/CSV/Markdown reporting.

---

### Task 1: Add benchmark runner utility

**Files:**
- Create: `tools/manual/benchmark_ab_indexing.py`
- Test: `tests/test_benchmark_ab_indexing.py`

Steps:
1. Write failing tests for mode parsing, metrics aggregation, and improvement-rate calculations.
2. Implement minimal benchmark runner with deterministic trial orchestration (`A-B-A-B...`).
3. Add JSONL emission per trial and summary structure.
4. Run targeted tests.

### Task 2: Add shell wrapper for easy execution

**Files:**
- Create: `scripts/benchmark_ab.sh`
- Modify: `tools/manual/README.md`

Steps:
1. Add a wrapper that accepts workspace path, repeats, and output directory.
2. Wire wrapper to new Python benchmark utility.
3. Document usage with concrete command examples.
4. Run smoke execution help command.

### Task 3: Add integrity + load checks in benchmark summary

**Files:**
- Modify: `tools/manual/benchmark_ab_indexing.py`
- Modify: `tests/test_benchmark_ab_indexing.py`

Steps:
1. Add summary fields for file/symbol/relation counts and CPU/memory indicators.
2. Add pass/fail gates for “no regressions in integrity” and “no load blow-up”.
3. Ensure report includes median/p95 and percent improvement.
4. Run tests and verify output format.

### Task 4: Verification and commit

**Files:**
- Modify: `docs/plans/2026-02-14-ab-initial-indexing-benchmark.md` (checklist/status)

Steps:
1. Run focused unit tests.
2. Run one local dry benchmark command against a small generated workspace.
3. Capture resulting report path and key metrics.
4. Commit with clear message.
