# Structural Stability Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce structural complexity in error-handling and input-validation paths without changing user-facing behavior.

**Architecture:** Introduce shared MCP validation helpers in `_util` and migrate selected tools to it. Narrow `search_repository` fallback behavior to explicit DB failure classes instead of blanket exception swallowing.

**Tech Stack:** Python 3.11, pytest, ruff, sqlite3

---

### Task 1: Add Shared MCP Validation/Error Helpers

**Files:**
- Modify: `src/sari/mcp/tools/_util.py`
- Test: `tests/test_low_coverage_mcp_tools_additional.py`

### Task 2: Migrate Tool-Level Validation To Shared Helpers

**Files:**
- Modify: `src/sari/mcp/tools/list_files.py`
- Modify: `src/sari/mcp/tools/read_file.py`
- Modify: `src/sari/mcp/tools/get_callers.py`

### Task 3: Refactor SearchRepository Fallback Boundaries

**Files:**
- Modify: `src/sari/core/repository/search_repository.py`
- Test: `tests/test_low_coverage_core_search_db.py`

### Task 4: Verify and Regression Check

**Files:**
- Test: `tests/test_low_coverage_mcp_tools_additional.py`
- Test: `tests/test_low_coverage_core_search_db.py`
- Verify: full suite + coverage
