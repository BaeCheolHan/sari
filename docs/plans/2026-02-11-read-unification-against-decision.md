# Read Unification `against` Decision Record

## Context
- We are unifying multiple read-like tools into a single `read` entrypoint.
- For `mode="diff_preview"`, we evaluated input strategies for comparison baseline (`against`).

## Decision (Approved)
- Adopt **Option 2** for initial release.
- Allowed `against` values:
  - `HEAD`
  - `WORKTREE`
  - `INDEX`

## Why Option 2
- Best balance between capability and implementation complexity.
- Covers the most common developer workflows:
  - compare with last commit (`HEAD`)
  - compare with current working files (`WORKTREE`)
  - compare with staged content (`INDEX`)
- Supports token-efficiency and lower bug surface compared to extended baseline formats.

## Deferred Scope (Future)
- **Option 3 is deferred** to a later phase.
- Planned future extensions:
  - `against="commit:<sha>"`
  - `against="snapshot:<id>"`

## Deferral Rationale
- Extended baseline formats increase:
  - invalid input/error branches (bad SHA, missing snapshot, repo-state issues)
  - validation and compatibility burden
  - LLM retry churn and token cost
- Current project priority is stability and predictable behavior.

## Follow-up Plan
1. Implement Option 2 in unified `read`.
2. Add comprehensive validation and clear `INVALID_ARGS` guidance for `against`.
3. Revisit Option 3 after initial rollout is stable and metrics confirm low retry/error rates.
