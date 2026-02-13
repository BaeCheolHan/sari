# Conservative Test Dedup Results (2026-02-12)

## Criteria
- Remove only clear redundancy.
- Keep slow/e2e tests unless they are true duplicates.

## Discovery
- Exact duplicate test-body clusters: `0`
- Exact duplicate test names: `0`

## Applied cleanup
- `tests/test_cli_commands.py`
  - Merged two equivalent legacy-routing tests into one parameterized test:
    - `test_cli_main_cmd_search_routes_to_legacy_cli`
    - `test_cli_main_cmd_status_routes_to_legacy_cli`
- `tests/test_cli_extra.py`
  - Merged two legacy re-export assertion tests into one table-driven test:
    - `test_legacy_daemon_commands_reexported_from_commands_module`
    - `test_legacy_status_and_maintenance_commands_reexported_from_commands_module`

## Verification
- `uv run pytest -q tests/test_cli_commands.py tests/test_cli_extra.py`
  - `26 passed`

## Notes on slow tests
- `tests/test_smart_daemon_e2e.py` includes real process/port lifecycle checks; retained.
- `tests/test_doctor_self_healing.py` contains heavier integration-style checks; retained.
