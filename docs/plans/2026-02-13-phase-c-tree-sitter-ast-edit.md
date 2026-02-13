# Phase C Tree-sitter Ast Edit Expansion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand `read(mode=ast_edit)` symbol-based edits beyond Python/JS by reusing tree-sitter parser coverage as the source of truth.

**Architecture:** Keep the existing Python AST and JS/TS heuristic paths for compatibility, and add a tree-sitter-backed symbol span resolver for other parser-supported languages. `ast_edit` selects replacement span by mode/language, applies optimistic lock/write flow unchanged, and returns the same stabilization envelope.

**Tech Stack:** Python 3.10+, existing `sari.core.parsers.ASTEngine` + `ParserFactory`, pytest.

---

### Task 1: Lock desired behavior with failing tests

**Files:**
- Modify: `tests/test_read_ast_edit.py`

**Step 1: Write failing tests**
1. Add a test where `mode=ast_edit` on a `.go` file with `symbol` succeeds when tree-sitter span resolver returns a valid line range.
2. Keep test independent of local tree-sitter runtime by monkeypatching resolver helper.

**Step 2: Run failing test**
Run: `pytest -q tests/test_read_ast_edit.py::test_ast_edit_symbol_mode_replaces_go_function_block_via_tree_sitter`
Expected: FAIL because no generic tree-sitter symbol path exists yet.

### Task 2: Implement minimal tree-sitter symbol resolver

**Files:**
- Modify: `src/sari/mcp/tools/read.py`

**Step 1: Add helper**
1. Introduce helper that maps extension to parser language via `ParserFactory.get_language`.
2. Parse with `ASTEngine.parse(...)`; if parse succeeds, extract symbols and find exact name match.
3. Return `(start_line, end_line)` only for valid spans.

**Step 2: Wire ast_edit symbol branch**
1. Keep `.py` and `.js/.jsx/.ts/.tsx` behavior unchanged.
2. For other extensions, use new tree-sitter helper.
3. Update validation message to reflect tree-sitter-backed multi-language support.

### Task 3: Verify and stabilize

**Files:**
- Modify: `src/sari/mcp/tools/registry.py`

**Step 1: Schema wording**
1. Update `read` tool schema description for `symbol` to remove Python-only wording.

**Step 2: Test run**
Run:
```bash
pytest -q tests/test_read_ast_edit.py tests/test_mcp_tools_extra.py
```
Expected: PASS.

**Step 3: Regression spot-check**
Run:
```bash
pytest -q tests/test_knowledge_tool.py tests/test_read_evidence_refs.py
```
Expected: PASS.
