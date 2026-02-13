# Path Pattern Rules

This document defines how `path_pattern` is interpreted in search flows.

## Matching Inputs

`path_pattern` is evaluated against three normalized candidates:

1. `rel_path` (workspace-relative path, e.g. `src/app/main.py`)
2. `path` (DB path, often `root_id/rel_path`, e.g. `rid/src/app/main.py`)
3. `rel_from_root` (first segment stripped from `rel_path` when present)
   - Example: `src/.idea/workspace.xml` -> `.idea/workspace.xml`

Normalization uses `/` separators and removes leading `./`.

## Pattern Engine

- Matching uses `fnmatch` semantics (`*`, `?`, `**`).
- `path_pattern` is treated as an include filter.
- SQL uses a broad prefilter for performance, then Python `fnmatch` is used for final exact filtering.

## Practical Examples

- `src/**` matches:
  - `src/app/main.py`
  - `rid/src/app/main.py`
- `.idea/**` matches:
  - `src/.idea/workspace.xml` (via `rel_from_root`)
- `.venv/**` matches:
  - `libs/.venv/site.py` (via `rel_from_root`)

## Notes

- For strict repository-scope matching, prefer explicit prefixes like `rid/src/**`.
- Exclude behavior is handled separately by `exclude_patterns`.
