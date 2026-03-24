# MCP Graph Regression Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** MCP `get_callers`/`call_graph`/`get_implementations`가 실제 저장된 심볼 관계를 안정적으로 반환하도록 회귀 테스트와 최소 로직 보강을 추가한다.

**Architecture:** 도구 계층(`src/sari/mcp/tools/symbol_tools.py`, `src/sari/mcp/tools/symbol_graph_tools.py`)과 저장소 조회 계층(`src/sari/db/repositories/lsp_tool_data_repository.py`) 사이의 심볼 매칭 규칙을 테스트로 고정한다. 먼저 RED 테스트로 현재 0건/편차 케이스를 재현하고, 조회 기준(정확 매칭 + 제한적 alias + path/context 힌트)을 단계적으로 보강한다. MCP 도구 외부 인터페이스(pack1 포맷/입력 스키마)는 유지한다.

**Tech Stack:** Python 3.14, pytest, sari MCP tool layer, SQLite repositories

---

### Task 1: Baseline Fixtures for Graph Regression

**Files:**
- Create: `tests/unit/mcp/fixtures/graph_regression_fixture.py`
- Test: `tests/unit/mcp/test_mcp_symbol_graph_regression.py`

**Step 1: Write the failing test**

```python
def test_fixture_builds_symbol_and_relation_baseline() -> None:
    fixture = build_graph_regression_fixture()
    assert fixture.repo_root != ""
    assert len(fixture.symbols) > 0
    assert len(fixture.relations) > 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/unit/mcp/test_mcp_symbol_graph_regression.py::test_fixture_builds_symbol_and_relation_baseline`
Expected: FAIL with `ImportError` or missing fixture builder.

**Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class GraphFixture:
    repo_root: str
    symbols: list[dict[str, object]]
    relations: list[dict[str, object]]
```

Add `build_graph_regression_fixture()` returning deterministic symbols/relations for:
- `status_endpoint` (positive control)
- `replace_file_data_many` (currently weak/0 caller control)
- class symbol + method symbol mixed names for alias coverage.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/unit/mcp/test_mcp_symbol_graph_regression.py::test_fixture_builds_symbol_and_relation_baseline`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/unit/mcp/fixtures/graph_regression_fixture.py tests/unit/mcp/test_mcp_symbol_graph_regression.py
git commit -m "test(mcp): add deterministic graph regression fixture"
```

### Task 2: RED Test for `get_callers` Symbol Resolution Drift

**Files:**
- Modify: `tests/unit/mcp/test_mcp_symbol_tools.py`
- Test: `tests/unit/mcp/test_mcp_symbol_graph_regression.py`

**Step 1: Write the failing test**

```python
def test_get_callers_returns_edges_for_method_symbol_even_when_class_symbol_exists() -> None:
    result = tool.call({"repo": repo_root, "symbol": "replace_file_data_many", "limit": 20})
    assert len(_items(result)) >= 1
```

Add companion test:

```python
def test_get_callers_keeps_status_endpoint_positive_control() -> None:
    result = tool.call({"repo": repo_root, "symbol": "status_endpoint", "limit": 20})
    assert len(_items(result)) >= 1
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/unit/mcp/test_mcp_symbol_graph_regression.py -k "get_callers"`
Expected: FAIL where regression symbol returns 0 callers.

**Step 3: Write minimal implementation**

In `src/sari/mcp/tools/symbol_tools.py`, tighten symbol resolution order:
1. exact symbol hit in same repo
2. symbol-key aware match
3. constrained alias fallback (same file/context only)

Do not add global fuzzy replacement.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/unit/mcp/test_mcp_symbol_graph_regression.py -k "get_callers"`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/unit/mcp/test_mcp_symbol_tools.py tests/unit/mcp/test_mcp_symbol_graph_regression.py src/sari/mcp/tools/symbol_tools.py
git commit -m "fix(mcp): harden get_callers symbol resolution"
```

### Task 3: RED Test for `call_graph` Consistency with `get_callers`

**Files:**
- Modify: `tests/unit/mcp/test_mcp_symbol_graph_regression.py`
- Modify: `src/sari/mcp/tools/symbol_graph_tools.py`

**Step 1: Write the failing test**

```python
def test_call_graph_consistent_with_get_callers_on_same_symbol() -> None:
    callers_items = _items(get_callers_tool.call({...}))
    graph_items = _items(call_graph_tool.call({...}))
    assert len(graph_items) >= len(callers_items)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/unit/mcp/test_mcp_symbol_graph_regression.py -k "call_graph_consistent"`
Expected: FAIL when `call_graph` returns summary-only record with missing edges.

**Step 3: Write minimal implementation**

In `src/sari/mcp/tools/symbol_graph_tools.py`, ensure `call_graph`:
- uses same symbol resolution helper as `get_callers`
- emits edge items when callers exist
- keeps existing pack1 summary record

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/unit/mcp/test_mcp_symbol_graph_regression.py -k "call_graph_consistent"`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/unit/mcp/test_mcp_symbol_graph_regression.py src/sari/mcp/tools/symbol_graph_tools.py
git commit -m "fix(mcp): align call_graph edges with get_callers"
```

### Task 4: RED Test for `get_implementations` Non-empty Interface Case

**Files:**
- Modify: `tests/unit/mcp/test_mcp_symbol_graph_regression.py`
- Modify: `src/sari/mcp/tools/symbol_graph_tools.py`

**Step 1: Write the failing test**

```python
def test_get_implementations_returns_candidates_for_interface_fixture() -> None:
    result = get_implementations_tool.call({"repo": repo_root, "symbol": "CollectionRuntimePort", "limit": 20})
    assert len(_items(result)) >= 1
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/unit/mcp/test_mcp_symbol_graph_regression.py -k "get_implementations"`
Expected: FAIL with 0 items.

**Step 3: Write minimal implementation**

In `src/sari/mcp/tools/symbol_graph_tools.py`:
- resolve interface symbol with path-aware query
- use repository implementation lookup with explicit fallback query path
- keep response schema unchanged

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/unit/mcp/test_mcp_symbol_graph_regression.py -k "get_implementations"`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/unit/mcp/test_mcp_symbol_graph_regression.py src/sari/mcp/tools/symbol_graph_tools.py
git commit -m "fix(mcp): improve get_implementations symbol lookup"
```

### Task 5: Exclude/Collectibility Contract for CI Scripts

**Files:**
- Modify: `tests/unit/mcp/test_mcp_file_collection_tools.py`
- Modify: `tests/unit/ci/test_ci_release_gate_mcp_probe.py`
- Modify: `src/sari/core/config_model.py` (only if contract intentionally changes)

**Step 1: Write the failing test**

```python
def test_index_file_rejects_shell_script_by_default_exclude() -> None:
    result = index_file_tool.call({"repo": repo_root, "relative_path": "tools/ci/run_installed_freshdb_smoke.sh"})
    assert _is_file_not_collectible(result)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/unit/mcp/test_mcp_file_collection_tools.py -k "shell_script_by_default_exclude"`
Expected: FAIL if current behavior drifts.

**Step 3: Write minimal implementation**

If failing, align collectibility check with `DEFAULT_COLLECTION_EXCLUDE_GLOBS` contract.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/unit/mcp/test_mcp_file_collection_tools.py -k "shell_script_by_default_exclude"`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/unit/mcp/test_mcp_file_collection_tools.py tests/unit/ci/test_ci_release_gate_mcp_probe.py src/sari/core/config_model.py
git commit -m "test(mcp): lock collectibility contract for ci scripts"
```

### Task 6: End-to-End MCP Regression Gate

**Files:**
- Modify: `tools/ci/release_gate_mcp_probe.py`
- Modify: `tests/unit/ci/test_release_gate_mcp_probe.py`
- Modify: `.github/workflows/release-pypi.yml` (if gate step ordering needs update)

**Step 1: Write the failing test**

```python
def test_call_flow_probe_fails_on_zero_candidate_edges_for_regression_symbol() -> None:
    ...
```

Add positive control asserting:
- `search_symbol(status_endpoint)` non-empty
- `get_callers(status_endpoint)` non-empty

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/unit/ci/test_release_gate_mcp_probe.py -k "regression_symbol"`
Expected: FAIL before probe enhancement.

**Step 3: Write minimal implementation**

Enhance `call_flow` probe to include optional symbol graph assertions:
- `SARI_MCP_PROBE_SYMBOL`
- `SARI_MCP_PROBE_EXPECT_CALLERS_MIN`

Default remains non-breaking for existing CI paths.

**Step 4: Run test to verify it passes**

Run:
- `uv run pytest -q tests/unit/ci/test_release_gate_mcp_probe.py -k "regression_symbol"`
- `uv run python tools/ci/release_gate_mcp_probe.py call_flow`

Expected: PASS.

**Step 5: Commit**

```bash
git add tools/ci/release_gate_mcp_probe.py tests/unit/ci/test_release_gate_mcp_probe.py .github/workflows/release-pypi.yml
git commit -m "ci(mcp): add symbol graph regression assertions to probe"
```

### Task 7: Full Verification and Handoff

**Files:**
- Modify: `README.md` (short regression verification section)
- Modify: `docs/handoff-2026-03-24-mcp-graph-regression.md`

**Step 1: Run focused suite**

Run:
- `uv run pytest -q tests/unit/mcp/test_mcp_symbol_tools.py`
- `uv run pytest -q tests/unit/mcp/test_mcp_symbol_graph_regression.py`
- `uv run pytest -q tests/unit/ci/test_release_gate_mcp_probe.py`

Expected: all PASS.

**Step 2: Run smoke**

Run:
- `tools/ci/run_installed_freshdb_smoke.sh`
- `python tools/ci/release_gate_mcp_probe.py call_flow`

Expected: PASS.

**Step 3: Document and commit**

```bash
git add README.md docs/handoff-2026-03-24-mcp-graph-regression.md
git commit -m "docs: handoff mcp graph regression hardening"
```

