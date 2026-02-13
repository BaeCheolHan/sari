"""Query utility helpers for DB search paths."""

from __future__ import annotations

import re
from typing import List, Tuple


def apply_root_filter(sql: str, root_id: str | None) -> Tuple[str, List[object]]:
    sql = str(sql or "").strip()
    if not sql:
        return sql, []
    lower_sql = sql.lower()
    insert_pos = len(sql)
    for token in (" group by ", " order by ", " limit ", " offset "):
        idx = lower_sql.find(token)
        if idx != -1:
            insert_pos = min(insert_pos, idx)
    head = sql[:insert_pos].rstrip()
    tail = sql[insert_pos:].lstrip()
    has_where = re.search(r"\bwhere\b", head, flags=re.IGNORECASE) is not None
    params: List[object] = []
    if root_id:
        if has_where:
            head += " AND root_id = ?"
        else:
            head += " WHERE root_id = ?"
        params.append(str(root_id))
    elif not has_where:
        head += " WHERE 1=1"
    sql = head if not tail else f"{head} {tail}"
    return sql, params
