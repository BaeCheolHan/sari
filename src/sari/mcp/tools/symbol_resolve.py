from __future__ import annotations

from typing import Any, Callable


def execute_symbol_resolve(
    args: dict[str, object],
    *,
    db: Any,
    logger: Any,
    roots: list[str],
    symbol_executor: Callable[[object, object, object, list[str]], dict[str, object]],
) -> dict[str, object]:
    """
    LSP on-demand symbol resolution stage.
    This layer exists to keep search pipeline explicit:
    candidate_search -> symbol_resolve -> normalize/pack.
    """
    return symbol_executor(args, db, logger, roots)

