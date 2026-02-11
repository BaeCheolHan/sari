import re
from typing import Optional, Tuple

def resolve_search_intent(query: str) -> Tuple[str, Optional[str]]:
    """
    v3 auto-inference logic with multilingual and complex pattern support.
    Returns: (resolved_type, inference_blocked_reason)
    """
    q = query.strip()

    # 1. SQL Blocker (Security) - Hardened regex
    sql_keywords = r"\b(SELECT|DROP|PRAGMA|ATTACH|DELETE|UPDATE|INSERT|UNION|CREATE|ALTER|TRUNCATE|GRANT|REVOKE)\b"
    if re.search(sql_keywords, q, re.IGNORECASE):
        return "code", "SQL-like keywords detected; API inference blocked for security."

    # 2. API Inference
    # Support for path-like structures and HTTP methods
    api_patterns = [
        r"^/",  # Starts with /
        r"\b(GET|POST|PUT|PATCH|DELETE)\s+/",  # Method + Path
        r"/[\w-]+/[\w-]+",  # Multiple path segments
        r"\b(endpoint|route|api|controller)\b"  # API-related keywords
    ]
    if any(re.search(p, q, re.IGNORECASE) for p in api_patterns):
        return "api", None

    # 3. Symbol Inference
    # Support for Multilingual (Unicode) identifiers, dot notation, or ::
    symbol_patterns = [
        r"^[\w_][\w\d_]*$",  # Single word identifier (multilingual)
        r"[\w_][\w\d_]*\.[\w_][\w\d_]*",  # Dot notation
        r"[\w_][\w\d_]*::[\w_][\w\d_]*"  # Double colon
    ]
    if any(re.search(p, q) for p in symbol_patterns):
        return "symbol", None

    # 4. Default to Code
    return "code", None
