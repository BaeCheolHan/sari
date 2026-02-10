# Entrypoint Routing Standard

## Goal
- Keep runtime startup behavior deterministic.
- Avoid duplicated startup branches across multiple entrypoints.

## Single Source Of Truth
- Runtime dispatch is centralized in `sari.main.main`.
- `python -m sari` and `python -m sari.mcp` must both route into `sari.main.main`.

## Routing Rules
1. `--cmd ...` and integrated CLI subcommands are handled first.
2. `--http-api` or `--transport http` routes to HTTP server path.
3. `--http-daemon` (or env equivalent) routes to daemon spawn path.
4. Otherwise stdio mode routes to MCP proxy path.

## Allowed Entrypoint Roles
- `sari.__main__`: thin shim to `sari.main.main`.
- `sari.mcp.__main__`: thin shim to `sari.main.main`.
- `sari.main`: only location that performs mode branching.

## Guardrails
- No direct legacy server boot in `sari.mcp.__main__`.
- New startup mode additions must be implemented in `sari.main` first.
- Tests must assert:
  - stdio path uses proxy
  - http path uses `_run_http_server`
  - mcp entrypoint delegates to `sari.main.main`
