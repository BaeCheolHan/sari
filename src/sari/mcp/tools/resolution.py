import os
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from sari.core.workspace import WorkspaceManager

def _is_safe_relative_path(rel: str) -> bool:
    if not rel or not rel.strip(): return False
    p = Path(rel.strip())
    if p.is_absolute(): return False
    for part in p.parts:
        if part in {"..", ""}: return False
        if ":" in part: return False
    return True

def resolve_root_ids(roots: List[str]) -> List[str]:
    if not roots or not WorkspaceManager: return []
    out: List[str] = []
    allow_legacy = os.environ.get("SARI_ALLOW_LEGACY", "").strip().lower() in {"1", "true", "yes", "on"}
    for r in roots:
        try:
            out.append(WorkspaceManager.root_id_for_workspace(r))
            if allow_legacy: out.append(WorkspaceManager.root_id(r))
        except Exception: continue
    return list(dict.fromkeys(out))

def resolve_db_path(input_path: str, roots: List[str], db: Optional[Any] = None) -> Optional[str]:
    """
    파일 시스템 경로를 Sari DB 경로로 변환 (Index-First Policy).
    """
    if not input_path: return None
    try:
        p_abs = str(Path(os.path.expanduser(input_path)).resolve())
    except Exception: return None

    # 1. Index-First: Check global database
    if db is not None:
        try:
            conn = getattr(db, "_read", None) or (db.get_read_connection() if hasattr(db, "get_read_connection") else None)
            if conn:
                row = conn.execute("SELECT path FROM files WHERE path = ? LIMIT 1", (p_abs,)).fetchone()
                if row: return row[0]
        except Exception: pass

    # 2. Roots Fallback
    resolved_roots = []
    for r in roots or []:
        try: resolved_roots.append(Path(r).expanduser().resolve())
        except Exception: continue

    sorted_roots = sorted(resolved_roots, key=lambda x: len(x.parts), reverse=True)
    p = Path(p_abs)
    
    for root_path in sorted_roots:
        try:
            if p == root_path or root_path in p.parents:
                rel = p.relative_to(root_path).as_posix()
                if not _is_safe_relative_path(rel) and p != root_path: continue
                rid = WorkspaceManager.root_id_for_workspace(str(root_path))
                return f"{rid}/{rel}" if rel != "." else rid
        except Exception: continue
    return None

def resolve_fs_path(db_path: str, roots: List[str]) -> Optional[str]:
    if not db_path or not roots: return None
    active_root_map = {}
    for r in roots:
        try:
            rid = WorkspaceManager.root_id_for_workspace(r)
            active_root_map[rid] = Path(r).expanduser().resolve()
        except Exception: continue

    sorted_rids = sorted(active_root_map.keys(), key=len, reverse=True)
    for rid in sorted_rids:
        if db_path.startswith(rid):
            if len(db_path) == len(rid): return str(active_root_map[rid])
            elif db_path[len(rid)] == "/":
                rel = db_path[len(rid) + 1:]
                if not _is_safe_relative_path(rel): continue
                candidate = (active_root_map[rid] / rel).resolve()
                if candidate == active_root_map[rid] or active_root_map[rid] in candidate.parents:
                    return str(candidate)
    return None

def resolve_repo_scope(repo: Optional[str], roots: List[str], db: Optional[Any] = None) -> Tuple[Optional[str], List[str]]:
    from ._util import _intersect_preserve_order # Circular safe aggregator helper
    allowed_root_ids = resolve_root_ids(roots)
    repo_raw = str(repo or "").strip()
    if not repo_raw: return None, allowed_root_ids

    q = repo_raw.lower()
    matched_root_ids: List[str] = []
    for r in roots or []:
        try:
            rp = Path(r).expanduser().resolve()
            if q == rp.name.lower() or q == str(rp).lower() or (q and q in rp.name.lower()):
                matched_root_ids.append(WorkspaceManager.root_id_for_workspace(str(rp)))
        except Exception: continue

    if db is not None:
        try:
            conn = getattr(db, "_read", None) or (db.get_read_connection() if hasattr(db, "get_read_connection") else None)
            if conn:
                rows = conn.execute("SELECT DISTINCT root_id FROM files WHERE LOWER(COALESCE(repo, '')) = LOWER(?)", (repo_raw,)).fetchall()
                matched_root_ids.extend([str(r[0]) for r in rows if r and r[0]])
        except Exception: pass

    if matched_root_ids:
        ids = list(dict.fromkeys(matched_root_ids))
        effective_ids = _intersect_preserve_order(allowed_root_ids, ids) if allowed_root_ids else ids
        # If matched by name, return None for repo filter per test expectation
        return None, effective_ids
    return repo_raw, allowed_root_ids
