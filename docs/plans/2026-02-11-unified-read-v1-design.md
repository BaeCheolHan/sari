# Unified Read v1 Design

## Goal
- Consolidate read-like tools into a single MCP entrypoint:
  - `read_file`
  - `read_symbol`
  - `get_snippet`
  - `dry_run_diff` (partial scope as `diff_preview`)
- Reduce tool-selection errors, token usage, and side effects.

## Scope Decision
- **Adopted for v1**: `against = HEAD | WORKTREE | INDEX`
- **Deferred for v2+**: `against = commit:<sha> | snapshot:<id>`

Related decision record:
- `docs/plans/2026-02-11-read-unification-against-decision.md`

## New Tool Surface
- Tool name: `read`
- Modes:
  - `file`
  - `symbol`
  - `snippet`
  - `diff_preview`

### Input Contract (v1)
- Required:
  - `mode`
  - `target`
- Optional common:
  - `offset` (line offset)
  - `limit` (line count / max items)
  - `preview_mode` (`none|snippet`, default `snippet`)
  - `max_preview_chars` (token-budget safety)
- Optional by mode:
  - `symbol`: `path` (disambiguation), `include_context`
  - `snippet`: `start_line`, `end_line`, `context_lines`
  - `diff_preview`: `against` (`HEAD|WORKTREE|INDEX`)

## Mode Routing
- `mode=file`:
  - Route to current `read_file` core path.
  - Keep pagination behavior.
- `mode=symbol`:
  - Route to `read_symbol` logic using symbol name/id.
- `mode=snippet`:
  - Route to `get_snippet` logic with bounded output.
- `mode=diff_preview`:
  - Route to subset of `dry_run_diff` logic.
  - Compare `target` against `against` baseline.

## Validation Rules
- Reject invalid combinations with explicit guidance.
- Examples:
  - `against` is valid only for `diff_preview`.
  - `start_line/end_line` valid only for `snippet`.
  - `path` disambiguation valid only for `symbol`.

Return style for invalid args:
- Code: `INVALID_ARGS`
- Message pattern: `"<param> is only valid for mode='<x>'. Remove it or switch mode."`

## Response Contract (Unified)
- Top-level:
  - `ok`
  - `mode`
  - `target`
  - `meta`
- `meta`:
  - `truncated`
  - `token_estimate`
  - `preview_degraded`
  - mode-specific fields (`against`, `resolved_symbol`, etc.)
- Content:
  - `text` (or `content`) with bounded payload
  - Optional `location`:
    - `{ "file": "...", "line": <int|null>, "end_line": <int|null> }`

## Token Budget Policy
- Default to bounded preview.
- Automatic degradation:
  - Shrink output when `limit` is large.
  - Enforce `max_preview_chars` hard cap.
- Report `meta.preview_degraded=true` when reduced.

## Stabilization Layer (v1 Addendum)

### Goal
- Add an observation + control + guidance layer to reduce:
  - token explosion
  - meaningless repeated reads
  - irrelevant target selection
  - goal drift
- Use unified `read` (Phase 1) as the single control point.

### Non-Goals (v1)
- No model reasoning (chain-of-thought) analysis or storage.
- No automatic code patch generation.
- No integration with external SAST/test execution MCPs.

### Stabilization Primitives (v1)

1) Session Metrics (observation)
- Collect:
  - `reads_count`, `reads_lines_total`, `reads_chars_total`
  - `search_count`
  - `read_after_search_ratio`
  - `avg_read_span`, `max_read_span`
  - `preview_degraded_count`
- Storage:
  - in-memory by default
  - optional `sqlite` table `session_metrics` (opt-in only)

2) Read Budget Guard (control)
- Default policy (initial values):
  - `max_reads_per_session = 25`
  - `max_total_read_lines = 2500`
  - `max_single_read_lines = 300`
  - `max_preview_chars = 12000`
- Behavior when limits are exceeded:
  - `SOFT_LIMIT`: degrade payload + attach search guidance hint
  - `HARD_LIMIT`: return `BUDGET_EXCEEDED` with actionable "use search to narrow scope" message

3) Relevance Guard (wrong-target prevention)
- Inputs:
  - requested read target (`path`/`symbol`)
  - recent search query and top-K result paths
  - workspace roots and excluded path rules
- Heuristics (lightweight):
  - warn when target is outside recent search top-K
  - apply exclusion policy for `vendor/`, `node_modules/`, `.git/`, `dist/`
- v1 default mode:
  - soft guidance first (warning + alternatives), not hard-block by default
  - hard block can be added as a future policy flag

4) Auto-Aggregation (context pollution prevention, v1-lite)
- Aggregate consecutive read outputs in a session with:
  - deduplication
  - structural compression only (no LLM summarization)
- v1 scope:
  - keep deterministic dedupe/compression behavior
  - `context_bundle_id` support is optional and can remain experimental

### Integration Points

Unified read hook (required):
- At `execute_read(...)` entry/exit:
  - update metrics
  - run budget checks
  - run relevance checks (configurable)
  - apply truncation/degradation policy

Unified search hook (recommended):
- At `execute_search(...)` completion:
  - store latest query + top-K candidate paths in session state
  - provide relevance baseline for subsequent reads

### API / Tool Surface (v1)
- Do not add new tools.
- Extend existing `read` and `search` responses with:
  - `meta.stabilization`
- Suggested shape:
  - `meta.stabilization = { budget_state, suggested_next_action, warnings[], metrics_snapshot }`

### Failure Modes & Policies
- Budget exceeded:
  - `code: BUDGET_EXCEEDED`
  - message: `"Read budget exceeded. Use search to narrow scope: ..."`
- Low relevance (soft):
  - `code: LOW_RELEVANCE`
  - message: `"This target seems unrelated. Try ..."`
- Always return actionable hints.

### Tests (must-have)
- budget soft/hard limit behavior
- relevance guard hit/miss behavior
- aggregation dedupe deterministic output
- metrics counting deterministic behavior

## Backward Compatibility Strategy
- Keep legacy tools temporarily.
- Legacy handlers should internally call unified `read`:
  - `read_file` -> `read(mode=file, ...)`
  - `read_symbol` -> `read(mode=symbol, ...)`
  - `get_snippet` -> `read(mode=snippet, ...)`
  - `dry_run_diff` -> `read(mode=diff_preview, ...)`
- Mark legacy tools as `deprecated` / `hidden` in registry after verification.

## Non-Goals (v1)
- No commit-hash or snapshot baseline in `diff_preview`.
- No cross-repo semantic diff.
- No heuristic mode auto-detection (explicit `mode` only).

## Risks & Mitigations
- Risk: Response payload explosion.
  - Mitigation: strict preview caps + degradation metadata.
- Risk: Ambiguous symbol resolution.
  - Mitigation: require `path` hint on ambiguity and provide actionable error.
- Risk: Regression from legacy wrappers.
  - Mitigation: compatibility tests for each old tool entrypoint.
