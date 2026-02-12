# Mega Bug Report Triage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Validate each `Mega_Bug_Report` item against actual `sari` code, fix confirmed bugs with tests, and mark completed items in the report.

**Architecture:** Work in strict per-item cycles: reproduce with a failing test, implement minimal fix, run targeted tests, then update the Obsidian report line with a completion marker. Keep each cycle isolated to avoid cross-item regression and enable partial delivery.

**Tech Stack:** Python, pytest, existing `sari` modules, Obsidian MCP note updates.

---

### Task 1: Establish Item Processing Contract

**Files:**
- Modify: `sari/Quality/Mega_Bug_Report.md`
- Test: `tests/*` (existing target modules)

**Step 1: Confirm completion marker format**
Run: N/A (user-aligned convention)
Expected: Use `[x]` prefix on completed lines.

**Step 2: Define per-item done criteria**
Run: N/A
Expected: Done means (a) bug confirmed or rejected with rationale, (b) confirmed bug fixed in code, (c) regression test exists and passes.

### Task 2: Execute Per-Item TDD Loop

**Files:**
- Modify: `src/sari/**`
- Test: `tests/**`
- Modify: `sari/Quality/Mega_Bug_Report.md`

**Step 1: Write failing test for one report item**
Run: `pytest <target test> -q`
Expected: FAIL for the intended bug condition.

**Step 2: Implement minimal fix**
Run: N/A
Expected: Smallest production code change that addresses the failing test.

**Step 3: Verify fix**
Run: `pytest <target test> -q`
Expected: PASS for new test, no regression in nearby tests.

**Step 4: Mark report item complete**
Run: N/A
Expected: Add completion marker (`[x]`) and short note (date + fixed test path).

### Task 3: Batch Verification and Handoff

**Files:**
- Modify: `sari/Quality/Mega_Bug_Report.md`

**Step 1: Run relevant grouped tests for touched modules**
Run: `pytest tests/<module area> -q`
Expected: PASS.

**Step 2: Summarize completed item numbers and code/test diffs**
Run: `git -C sari diff -- <files>`
Expected: Clear mapping from report item to fix.

**Step 3: Prepare next item queue**
Run: N/A
Expected: Remaining unchecked items prioritized by severity and fix cost.
