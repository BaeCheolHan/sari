import re
import time
import unicodedata
import fnmatch
from pathlib import Path
from typing import List


def glob_to_like(pattern: str) -> str:
    """Convert glob-style pattern to SQL LIKE pattern for 1st-pass filtering."""
    if not pattern:
        return "%"

    # Better glob-to-like conversion
    res = pattern.replace("**", "%").replace("*", "%").replace("?", "_")

    # If no wildcards were present in the ORIGINAL pattern, 
    # we don't force-wrap it in %. Let the caller handle partial vs exact.
    # However, for 1st-pass filtering, we often WANT a partial match.
    # We change it to only wrap if it doesn't look like an absolute or explicit relative path.
    if not ("*" in pattern or "?" in pattern):
        # If it's a simple name without slashes, we can keep the "contains" behavior,
        # but for paths with slashes, we should be more precise.
        if "/" not in pattern:
            res = f"%{res}%"
    
    # Ensure it starts/ends correctly for directory patterns
    if pattern.endswith("/**"):
        res = res.rstrip("%") + "%"

    while "%%" in res:
        res = res.replace("%%", "%")
    return res


def _normalize_match_path(value: str) -> str:
    text = str(value or "").replace("\\", "/")
    return text.lstrip("./")


def match_path_pattern(path: str, rel_path: str, pattern: str) -> bool:
    if not pattern:
        return True
    pat = _normalize_match_path(pattern)
    norm_path = _normalize_match_path(path)
    norm_rel_path = _normalize_match_path(rel_path or norm_path)
    rel_from_root_raw = norm_rel_path.split("/", 1)[1] if "/" in norm_rel_path else norm_rel_path
    rel_from_root = _normalize_match_path(rel_from_root_raw)
    candidates = (norm_rel_path, norm_path, rel_from_root)
    return any(fnmatch.fnmatchcase(candidate, pat) for candidate in candidates if candidate)


def get_file_extension(path: str) -> str:
    ext = Path(path).suffix
    return ext[1:].lower() if ext else ""


def calculate_recency_score(mtime: int, base_score: float) -> float:
    now = time.time()
    age_days = (now - mtime) / 86400
    if age_days < 1:
        boost = 1.5
    elif age_days < 7:
        boost = 1.3
    elif age_days < 30:
        boost = 1.1
    else:
        boost = 1.0

    # Ensure boost works even if base_score is 0 (bias added)
    return (base_score + 0.1) * boost


def extract_terms(q: str) -> List[str]:
    # Use regex to extract quoted phrases or space-separated words
    raw = re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', q or "")
    out: List[str] = []
    for group in raw:
        # group is a tuple of (double_quoted, single_quoted, bare_word)
        t = group[0] or group[1] or group[2]
        t = t.strip()
        if not t or t in {"AND", "OR", "NOT"}:
            continue
        if ":" in t and len(t.split(":", 1)[0]) <= 10:
            t = t.split(":", 1)[1]
        t = t.strip()
        if t:
            out.append(t)
    return out


def count_matches(
        content: str,
        query: str,
        use_regex: bool,
        case_sensitive: bool) -> int:
    if not query:
        return 0
    if use_regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            return len(re.findall(query, content, flags))
        except re.error:
            return 0
    else:
        normalized_query = unicodedata.normalize("NFKC", query)
        normalized_content = unicodedata.normalize("NFKC", content)
        if not case_sensitive:
            normalized_query = normalized_query.casefold()
            normalized_content = normalized_content.casefold()
        
        # Count overlapping matches
        count = 0
        start = 0
        while True:
            idx = normalized_content.find(normalized_query, start)
            if idx == -1:
                break
            count += 1
            start = idx + 1  # Move forward by 1 to catch overlaps
        return count


def snippet_around(
        content: object,
        terms: List[str],
        max_lines: int,
        highlight: bool = True,
        case_sensitive: bool = False) -> str:
    if not content:
        return ""
    if isinstance(content, (bytes, bytearray)):
        content = content.decode("utf-8", errors="ignore")
    elif not isinstance(content, str):
        content = str(content)

    if max_lines <= 0:
        return ""
    lines = content.splitlines()
    if not lines:
        return ""

    lower_lines = [line_text if case_sensitive else line_text.lower() for line_text in lines]
    lower_terms = [t if case_sensitive else t.lower() for t in terms if t.strip()]

    if not lower_terms:
        return "\n".join(f"L{i+1}: {ln}" for i,
                         ln in enumerate(lines[:max_lines]))

    # Score per line
    # +1 per match, +5 if definition (def/class) AND match
    line_scores = [0] * len(lines)
    def_pattern = re.compile(
        r"\b(class|def|function|struct|interface|type)\s+",
        re.IGNORECASE)

    has_any_match = False
    for i, line_lower in enumerate(lower_lines):
        score = 0
        for t in lower_terms:
            if t in line_lower:
                score += 1

        if score > 0:
            has_any_match = True
            if def_pattern.search(line_lower):
                score += 5

        line_scores[i] = score

    if not has_any_match:
        return "\n".join(f"L{i+1}: {ln}" for i,
                         ln in enumerate(lines[:max_lines]))

    # Find best window (Sliding Window)
    window_size = min(len(lines), max_lines)
    current_score = sum(line_scores[:window_size])
    best_window_score = current_score
    best_start = 0

    for i in range(len(lines) - window_size):
        # Move window: subtract line at 'i', add line at 'i + window_size'
        current_score = current_score - line_scores[i] + line_scores[i + window_size]
        if current_score > best_window_score:
            best_window_score = current_score
            best_start = i + 1

    # Extract window
    start_idx = best_start
    end_idx = start_idx + window_size

    out_lines = []
    ordered_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in sorted((t for t in terms if t.strip()), key=len, reverse=True):
        norm = term if case_sensitive else term.lower()
        if norm in seen_terms:
            continue
        seen_terms.add(norm)
        ordered_terms.append(term)
    highlight_pattern = None
    if ordered_terms:
        joined = "|".join(re.escape(t) for t in ordered_terms)
        highlight_pattern = re.compile(joined, 0 if case_sensitive else re.IGNORECASE)

    for i in range(start_idx, end_idx):
        line = lines[i]
        if highlight and highlight_pattern is not None:
            line = highlight_pattern.sub(lambda m: f">>>{m.group(0)}<<<", line)

        out_lines.append(f"L{i+1}: {line}")

    return "\n".join(out_lines)
