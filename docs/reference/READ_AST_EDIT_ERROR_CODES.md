# `read(mode=ast_edit)` Error Codes

This document defines error semantics for `ast_edit` and the client-side action mapping.

## Client Mapping

| Error Code | Meaning | `client_action` | Recommended Client Behavior |
|---|---|---|---|
| `VERSION_CONFLICT` | Target content changed after the caller read it | `re_read` | Re-run `read`, then retry with latest `expected_version_hash` |
| `SYMBOL_KIND_INVALID` | Unsupported `symbol_kind` value | `fix_args` | Replace with enum value: `function/method/class/interface/struct/trait/enum/module` |
| `SYMBOL_RESOLUTION_FAILED` | Symbol span could not be resolved (runtime/parser/hint issue) | `search_symbol` | Use `search` or `read_symbol` to refresh symbol/hints |
| `SYMBOL_NOT_FOUND` | Symbol name not found in target | `search_symbol` | Verify symbol name or pass `symbol_qualname` |
| `SYMBOL_BLOCK_MISMATCH` | `old_text` not found inside selected symbol block | `adjust_old_text` | Recompute `old_text` from selected symbol block or omit `old_text` |
| `INVALID_ARGS` | Invalid argument set | `fix_args` | Fix request payload and retry |
| `NOT_INDEXED` | Target out of scope or not indexed | `reindex` | Ensure workspace scope/indexing before retry |
| `IO_ERROR` | Read/write I/O failure | `retry` | Retry after permission/state check |

## Preview Mode

Use `ast_edit_preview=true` to receive a preview without writing file contents.

- Response includes:
  - `preview=true`
  - `updated=false`
  - `change_preview` (unified diff snippet)
  - `preview_version_hash` (hash of edited content candidate)
- Apply flow:
  1. Call preview with `ast_edit_preview=true`
  2. Validate `change_preview`
  3. Re-call `ast_edit` with `expected_version_hash` from current file snapshot
