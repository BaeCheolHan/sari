# Package Layout Reorganization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve navigability by grouping core and collection modules into role-based subpackages.

**Architecture:** Introduce domain subpackages (`core/repo`, `core/language`, `services/collection/l5/lsp`) and move concrete modules there. Legacy compatibility shims were used during migration, then removed after call-site cutover and tests stabilized.

**Tech Stack:** Python 3.11, pytest, package-relative imports

---

### Task 1: Add failing layout compatibility tests

**Files:**
- Create: `tests/unit/services/collection/test_package_layout_reorg.py`

1. Add tests that import new subpackage paths and old paths.
2. Assert key symbols can be imported from both locations.
3. Run tests and confirm failure before implementation.

### Task 2: Reorganize `core` into subpackages

**Files:**
- Create: `src/sari/core/repo/__init__.py`
- Create: `src/sari/core/repo/context_resolver.py`
- Create: `src/sari/core/repo/identity.py`
- Create: `src/sari/core/repo/resolver.py`
- Create: `src/sari/core/language/__init__.py`
- Create: `src/sari/core/language/registry.py`
- Create: `src/sari/core/language/provision_policy.py`
- Modify: `src/sari/core/repo_context_resolver.py`
- Modify: `src/sari/core/repo_identity.py`
- Modify: `src/sari/core/repo_resolver.py`
- Modify: `src/sari/core/language_registry.py`
- Modify: `src/sari/core/lsp_provision_policy.py`

1. Move implementations to subpackages.
2. Leave old modules as compatibility re-export shims.

### Task 3: Reorganize collection LSP support modules

**Files:**
- Create: `src/sari/services/collection/lsp/__init__.py`
- Create: `src/sari/services/collection/lsp/*.py` (moved implementation modules)
- Modify: `src/sari/services/collection/lsp_*.py` (convert to compatibility shims)

1. Move extracted LSP helper services under `collection/lsp/`.
2. Keep old `lsp_*.py` modules as import-compatible shims.

### Task 4: Add package role documentation

**Files:**
- Create: `src/sari/core/README.md`
- Create: `src/sari/services/collection/README.md`

1. Document each package/subpackage role.
2. Explicitly describe where to add new modules.

### Task 5: Verify

**Files:**
- Test: `tests/unit/services/collection/test_package_layout_reorg.py`
- Test: selected collection/core tests touching moved modules

Commands:
- `pytest -q tests/unit/services/collection/test_package_layout_reorg.py`
- `pytest -q tests/unit/test_pipeline_perf_service.py tests/unit/test_batch17_performance_hardening.py`

---

## Completion Update (2026-02-26)

- `core` legacy shim modules removed:
  - `sari/core/language_registry.py`
  - `sari/core/lsp_provision_policy.py`
  - `sari/core/repo_context_resolver.py`
  - `sari/core/repo_identity.py`
  - `sari/core/repo_resolver.py`
- `services/collection` legacy shim modules removed:
  - root shim files (`l3_*.py`, `l4_*.py`, `l5_*.py`, `lsp_*.py`)
  - root legacy files (`event_watcher.py`, `scanner.py`, `l2_job_processor.py`)
  - shim packages (`lsp/`, `l3_stages/`, `l3_language_processors/`)
- Canonical import rules now enforced by tests:
  - no legacy shim imports in `src`
  - no legacy shim imports in `tests` (except explicit contract-file string literals)
