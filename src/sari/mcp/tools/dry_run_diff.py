import ast
import json
import difflib
import os
import shutil
import subprocess
from typing import Mapping, TypeAlias

from sari.core.policy_engine import load_read_policy
from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_text,
    pack_encode_id,
    pack_error,
    ErrorCode,
    resolve_db_path,
    resolve_fs_path,
    sanitize_error_message,
)

ToolArgs: TypeAlias = dict[str, object]
ToolResult: TypeAlias = dict[str, object]


def _bounded_int(value: object, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except Exception:
        parsed = default
    return max(min_value, min(parsed, max_value))


def _read_current(db: object, db_path: str, roots: list[str]) -> str:
    """DB 또는 파일 시스템으로부터 현재 파일의 전체 내용을 읽어옵니다."""
    fs_path = resolve_fs_path(db_path, roots)
    if fs_path:
        try:
            with open(fs_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            pass
    raw = db.read_file_raw(db_path) if hasattr(db, "read_file_raw") else db.read_file(db_path)
    return raw or ""


def _read_git_baseline(db_path: str, roots: list[str], against: str) -> tuple[str, bool]:
    fs_path = resolve_fs_path(db_path, roots)
    if not fs_path:
        return "", False
    abs_path = os.path.abspath(fs_path)
    repo_probe = subprocess.run(
        ["git", "-C", os.path.dirname(abs_path), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=3.0,
    )
    if repo_probe.returncode != 0:
        return "", False
    repo_root = repo_probe.stdout.strip()
    if not repo_root:
        return "", False
    rel_path = os.path.relpath(abs_path, repo_root).replace("\\", "/")
    spec = f"HEAD:{rel_path}" if against == "HEAD" else f":{rel_path}"
    show_proc = subprocess.run(
        ["git", "-C", repo_root, "show", spec],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=5.0,
    )
    if show_proc.returncode != 0:
        return "", False
    return show_proc.stdout.decode("utf-8", errors="ignore"), True


def _read_baseline(db: object, db_path: str, roots: list[str], against: str) -> tuple[str, bool]:
    baseline = str(against or "WORKTREE").strip().upper()
    if baseline == "WORKTREE":
        return _read_current(db, db_path, roots), False
    if baseline in {"HEAD", "INDEX"}:
        text, ok = _read_git_baseline(db_path, roots, baseline)
        if ok:
            return text, False
        return _read_current(db, db_path, roots), True
    return _read_current(db, db_path, roots), True


def _syntax_check(path: str, content: str) -> ToolResult:
    """
    제안된 수정 사항에 대해 가벼운 구문 체크(Syntax Check)를 수행합니다.
    Python의 경우 AST 파싱을, JSON의 경우 json.loads를 시도합니다.
    """
    import sys
    runtime = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if path.endswith(".py"):
        try:
            ast.parse(content)
            return {"syntax_ok": True, "runtime": runtime}
        except SyntaxError as e:
            return {
                "syntax_ok": False, 
                "syntax_error": f"Line {e.lineno}, Col {e.offset}: {e.msg}",
                "runtime": runtime,
                "hint": "Check if you used syntax from a newer Python version than " + runtime
            }
        except Exception as e:
            return {"syntax_ok": False, "syntax_error": sanitize_error_message(e, "python syntax validation failed"), "runtime": runtime}
    if path.endswith(".json"):
        try:
            json.loads(content)
            return {"syntax_ok": True, "runtime": runtime}
        except Exception as e:
            return {"syntax_ok": False, "syntax_error": sanitize_error_message(e, "json syntax validation failed"), "runtime": runtime}
    return {"syntax_ok": True, "runtime": runtime}

def _maybe_lint(path: str, content: str) -> ToolResult:
    """
    설정된 경우 Ruff나 ESLint와 같은 외부 린터를 사용하여 제안된 수정을 검사합니다.
    (SARI_DRYRUN_LINT 환경변수가 활성화된 경우)
    """
    enabled = os.environ.get("SARI_DRYRUN_LINT", "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"lint_skipped": True, "lint_reason": "disabled"}
    ext = path.lower()
    if ext.endswith(".py") and shutil.which("ruff"):
        try:
            proc = subprocess.run(
                ["ruff", "check", "--quiet", "--stdin-filename", path, "--", "-"],
                input=content.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5.0,
            )
            output = (proc.stdout or b"").decode("utf-8", errors="ignore").strip()
            err = (proc.stderr or b"").decode("utf-8", errors="ignore").strip()
            return {
                "lint_tool": "ruff",
                "lint_ok": proc.returncode == 0,
                "lint_output": output,
                "lint_error": err,
            }
        except Exception as e:
            return {"lint_ok": False, "lint_error": sanitize_error_message(e, "ruff execution failed"), "lint_tool": "ruff"}
    if (ext.endswith(".js") or ext.endswith(".ts") or ext.endswith(".jsx") or ext.endswith(".tsx")) and shutil.which("eslint"):
        try:
            proc = subprocess.run(
                ["eslint", "--stdin", "--stdin-filename", path, "--"],
                input=content.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=8.0,
            )
            output = (proc.stdout or b"").decode("utf-8", errors="ignore").strip()
            err = (proc.stderr or b"").decode("utf-8", errors="ignore").strip()
            return {
                "lint_tool": "eslint",
                "lint_ok": proc.returncode == 0,
                "lint_output": output,
                "lint_error": err,
            }
        except Exception as e:
            return {"lint_ok": False, "lint_error": sanitize_error_message(e, "eslint execution failed"), "lint_tool": "eslint"}
    return {"lint_skipped": True, "lint_reason": "tool_not_found"}


def build_dry_run_diff(args: ToolArgs, db: object, roots: list[str]) -> ToolResult:
    """드라이런 분석을 수행하고 차이점, 구문 상태, 린트 결과를 포함한 데이터 딕셔너리를 빌드합니다."""
    policy = load_read_policy()
    path = str(args.get("path") or "").strip()
    raw_content = args.get("content")
    if not path or raw_content is None:
        raise ValueError("path and content are required")
    new_content = str(raw_content)
    against = str(args.get("against") or "WORKTREE").strip().upper()
    max_preview_chars = _bounded_int(
        args.get("max_preview_chars", policy.max_preview_chars),
        policy.max_preview_chars,
        min_value=100,
        max_value=policy.max_preview_chars,
    )
    db_path = resolve_db_path(path, roots)
    if not db_path:
        raise ValueError("path is out of workspace scope")
    current, fallback_used = _read_baseline(db, db_path, roots, against)
    
    # 1. 통합 차이(Unified Diff) 생성
    diff_lines = list(
        difflib.unified_diff(
            current.splitlines(),
            new_content.splitlines(),
            fromfile=f"{db_path} (current)",
            tofile=f"{db_path} (proposed)",
            lineterm="",
        )
    )
    diff_text = "\n".join(diff_lines)
    diff_truncated = False
    if len(diff_text) > max_preview_chars:
        diff_text = diff_text[:max_preview_chars]
        diff_truncated = True
    
    # 2. 구문 및 린트 검사
    syntax = _syntax_check(path, new_content)
    lint = _maybe_lint(path, new_content)
    
    payload = {
        "path": db_path,
        "diff": diff_text,
        "against": against,
        "against_fallback": fallback_used,
        "diff_truncated": diff_truncated,
    }
    payload.update(syntax)
    payload.update(lint)
    return payload


def execute_dry_run_diff(args: object, db: object, roots: list[str]) -> ToolResult:
    """
    파일 수정 전, 변경 사항을 미리 보고(Dry-run) 구문 오류나 스타일 위반을 사전에 점검하는 도구입니다.
    실제 파일 시스템에 영향을 주지 않고 안전하게 수정을 검증할 수 있습니다.
    """
    if not isinstance(args, Mapping):
        return mcp_response(
            "dry_run_diff",
            lambda: pack_error("dry_run_diff", ErrorCode.INVALID_ARGS, "'args' must be an object"),
            lambda: {
                "error": {
                    "code": ErrorCode.INVALID_ARGS.value,
                    "message": "'args' must be an object",
                },
                "isError": True,
            },
        )
    args_map: ToolArgs = dict(args)

    try:
        payload = build_dry_run_diff(args_map, db, roots)
    except ValueError as e:
        msg = str(e)
        return mcp_response(
            "dry_run_diff",
            lambda: pack_error("dry_run_diff", ErrorCode.INVALID_ARGS, msg),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": msg}, "isError": True},
        )
    except Exception as e:
        msg = sanitize_error_message(e, "dry_run_diff failed")
        return mcp_response(
            "dry_run_diff",
            lambda: pack_error("dry_run_diff", ErrorCode.INTERNAL, msg, fields={"reason_code": "DRY_RUN_DIFF_FAILED"}),
            lambda: {"error": {"code": ErrorCode.INTERNAL.value, "message": msg, "data": {"reason_code": "DRY_RUN_DIFF_FAILED"}}, "isError": True},
        )

    def build_pack() -> str:
        """PACK1 형식의 응답을 생성합니다."""
        lines = [
            pack_header(
                "dry_run_diff",
                {
                    "path": pack_encode_id(payload["path"]),
                    "against": pack_encode_id(payload.get("against", "WORKTREE")),
                },
                returned=1,
            )
        ]
        lines.append(pack_line("m", {"syntax_ok": str(bool(payload.get("syntax_ok", True))).lower()}))
        if payload.get("against_fallback"):
            lines.append(pack_line("m", {"against_fallback": "true"}))
        if payload.get("runtime"):
            lines.append(pack_line("m", {"runtime": pack_encode_text(payload["runtime"])}))
        if payload.get("hint"):
            lines.append(pack_line("m", {"hint": pack_encode_text(payload["hint"])}))
        if payload.get("syntax_error"):
            lines.append(pack_line("m", {"syntax_error": pack_encode_text(payload["syntax_error"])}))
        if "lint_ok" in payload:
            lines.append(pack_line("m", {"lint_ok": str(bool(payload.get("lint_ok"))).lower(), "lint_tool": pack_encode_text(payload.get("lint_tool", ""))}))
        if payload.get("lint_error"):
            lines.append(pack_line("m", {"lint_error": pack_encode_text(payload["lint_error"])}))
        lines.append(pack_line("d", single_value=pack_encode_text(payload.get("diff", ""))))
        return "\n".join(lines)

    return mcp_response(
        "dry_run_diff",
        build_pack,
        lambda: payload,
    )
