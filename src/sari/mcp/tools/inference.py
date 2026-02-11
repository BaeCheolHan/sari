import re
from typing import Optional, Tuple

def resolve_search_intent(query: str) -> Tuple[str, Optional[str]]:
    """
    v3 auto-inference logic.
    Returns: (resolved_type, inference_blocked_reason)
    """
    q = query.strip()
    
    # 1. SQL Blocker (Security)
    sql_keywords = r"\b(SELECT|DROP|PRAGMA|ATTACH|DELETE|UPDATE|INSERT|UNION|CREATE|ALTER)\b"
    if re.search(sql_keywords, q, re.IGNORECASE):
        return "code", "SQL-like keywords detected; API inference blocked for security."

    # 2. API Inference
    # Pattern: starts with / or contains / with HTTP methods
    api_patterns = [
        r"^/",
        r"\b(GET|POST|PUT|PATCH|DELETE)\s+/",
        r"/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+"
    ]
    if any(re.search(p, q, re.IGNORECASE) for p in api_patterns):
        return "api", None

    # 3. Symbol Inference
    # Pattern: Single word identifier, dot notation, or ::
    symbol_patterns = [
        r"^[a-zA-Z_][a-zA-Z0-9_]*$",
        r"[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*",
        r"[a-zA-Z_][a-zA-Z0-9_]*::[a-zA-Z_][a-zA-Z0-9_]*"
    ]
    if any(re.search(p, q) for p in symbol_patterns):
        return "symbol", None

    # 4. Default to Code
    return "code", None
