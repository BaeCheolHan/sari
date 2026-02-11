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
