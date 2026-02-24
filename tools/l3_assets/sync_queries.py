#!/usr/bin/env python3
"""L3 query asset sync/validation tool."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import URLError
from urllib.request import urlopen


_LANGUAGES = ("java", "javascript", "python", "typescript")
_OFFICIAL_URLS = {
    "java": "https://raw.githubusercontent.com/tree-sitter/tree-sitter-java/master/queries/tags.scm",
    "javascript": "https://raw.githubusercontent.com/tree-sitter/tree-sitter-javascript/master/queries/tags.scm",
    "python": "https://raw.githubusercontent.com/tree-sitter/tree-sitter-python/master/queries/tags.scm",
    "typescript": "https://raw.githubusercontent.com/tree-sitter/tree-sitter-typescript/master/queries/tags.scm",
}
_NVIM_URLS = {
    "java": "https://raw.githubusercontent.com/nvim-treesitter/nvim-treesitter/master/queries/java/tags.scm",
    "javascript": "https://raw.githubusercontent.com/nvim-treesitter/nvim-treesitter/master/queries/javascript/tags.scm",
    "python": "https://raw.githubusercontent.com/nvim-treesitter/nvim-treesitter/master/queries/python/tags.scm",
    "typescript": "https://raw.githubusercontent.com/nvim-treesitter/nvim-treesitter/master/queries/typescript/tags.scm",
}
_SUPPLEMENTS = {
    "java": """
(package_declaration (scoped_identifier) @name) @symbol.module
(package_declaration (identifier) @name) @symbol.module
(interface_declaration name: (identifier) @name) @symbol.interface
(annotation_type_declaration name: (identifier) @name) @symbol.interface
(record_declaration name: (identifier) @name) @symbol.class
(enum_declaration name: (identifier) @name) @symbol.enum
(constructor_declaration name: (identifier) @name) @symbol.method
(field_declaration (variable_declarator name: (identifier) @name) @symbol.field)
(enum_constant name: (identifier) @name) @symbol.enum_constant
""".strip(),
    "javascript": """
(class_declaration name: (identifier) @name) @definition.class
(method_definition name: (property_identifier) @name) @definition.method
(function_declaration name: (identifier) @name) @definition.function
(lexical_declaration (variable_declarator name: (identifier) @name value: [(arrow_function) (function)])) @definition.function
(variable_declaration (variable_declarator name: (identifier) @name value: [(arrow_function) (function)])) @definition.function
(assignment_expression left: (identifier) @definition.function right: [(arrow_function) (function)])
(assignment_expression left: (member_expression property: (property_identifier) @definition.function) right: [(arrow_function) (function)])
(pair key: (property_identifier) @name value: [(arrow_function) (function)]) @definition.function
(call_expression function: (identifier) @definition.function arguments: (arguments [(arrow_function) (function)]))
(call_expression function: (member_expression property: (property_identifier) @definition.function) arguments: (arguments [(arrow_function) (function)]))
(pair key: (property_identifier) @symbol.field)
(pair key: (string) @symbol.field)
(pair key: (computed_property_name) @symbol.field)
(shorthand_property_identifier) @symbol.field
(shorthand_property_identifier_pattern) @symbol.field
(variable_declarator name: (identifier) @name) @symbol.field
(catch_clause parameter: (identifier) @symbol.variable)
""".strip(),
}


def _required_files(root: Path) -> tuple[Path, ...]:
    return (
        root / "manifest.json",
        root / "mappings" / "default.yaml",
        root / "queries" / "java" / "outline.scm",
        root / "queries" / "javascript" / "outline.scm",
        root / "queries" / "typescript" / "outline.scm",
        root / "queries" / "python" / "outline.scm",
    )


def _fetch_text(url: str, *, timeout_sec: float) -> str:
    try:
        with urlopen(url, timeout=timeout_sec) as resp:  # noqa: S310 - explicit trusted upstream only
            data = resp.read()
        return data.decode("utf-8", errors="ignore")
    except URLError as exc:
        # Some local environments have broken cert store for urllib.
        # Fallback to curl keeps sync usable without changing policy logic.
        proc = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--connect-timeout",
                str(max(1, int(timeout_sec))),
                "--max-time",
                str(max(1, int(timeout_sec * 2))),
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"curl_fetch_failed:{url}:{proc.stderr.strip()}") from exc
        return proc.stdout


def _read_source(*, lang: str, base_dir: Path | None, remote_url: str, timeout_sec: float) -> str:
    if base_dir is not None:
        local_path = base_dir / lang / "tags.scm"
        return local_path.read_text(encoding="utf-8")
    return _fetch_text(remote_url, timeout_sec=timeout_sec)


def _safe_optional_source(*, lang: str, base_dir: Path | None, remote_url: str, timeout_sec: float) -> str:
    try:
        return _read_source(lang=lang, base_dir=base_dir, remote_url=remote_url, timeout_sec=timeout_sec)
    except (OSError, URLError, ValueError, TypeError, RuntimeError):
        return ""


def _merge_query_sources(*, lang: str, official_text: str, nvim_text: str) -> str:
    if lang == "javascript":
        supplement = _SUPPLEMENTS.get(lang, "").strip()
        return (supplement + "\n") if supplement != "" else ""
    official = official_text.strip()
    overlay = nvim_text.strip()
    merged = official
    if overlay != "" and official != overlay:
        merged = merged + "\n\n; overlay:nvim-treesitter\n" + overlay
    supplement = _SUPPLEMENTS.get(lang, "").strip()
    if supplement != "":
        merged = merged + "\n\n; supplement:sari\n" + supplement
    return merged + "\n"


def _write_outline(*, assets_root: Path, lang: str, merged_text: str) -> None:
    query_dir = assets_root / "queries" / lang
    query_dir.mkdir(parents=True, exist_ok=True)
    (query_dir / "outline.scm").write_text(merged_text, encoding="utf-8")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sync_queries(
    *,
    assets_root: Path,
    official_root: Path | None,
    nvim_root: Path | None,
    timeout_sec: float,
) -> dict[str, object]:
    details: dict[str, dict[str, str]] = {}
    for lang in _LANGUAGES:
        try:
            official_text = _read_source(
                lang=lang,
                base_dir=official_root,
                remote_url=_OFFICIAL_URLS[lang],
                timeout_sec=timeout_sec,
            )
            nvim_text = _safe_optional_source(
                lang=lang,
                base_dir=nvim_root,
                remote_url=_NVIM_URLS[lang],
                timeout_sec=timeout_sec,
            )
        except (OSError, URLError, ValueError, TypeError, RuntimeError) as exc:
            raise RuntimeError(f"sync_failed:{lang}:{type(exc).__name__}:{exc}") from exc
        merged = _merge_query_sources(lang=lang, official_text=official_text, nvim_text=nvim_text)
        _write_outline(assets_root=assets_root, lang=lang, merged_text=merged)
        details[lang] = {
            "official_sha256": _sha256_text(official_text),
            "nvim_sha256": _sha256_text(nvim_text),
            "merged_sha256": _sha256_text(merged),
        }
    return {
        "languages": sorted(_LANGUAGES),
        "official_source": str(official_root) if official_root is not None else "remote",
        "nvim_source": str(nvim_root) if nvim_root is not None else "remote",
        "details": details,
    }


def run(
    *,
    assets_root: Path,
    lock_path: Path | None,
    sync: bool = False,
    official_root: Path | None = None,
    nvim_root: Path | None = None,
    fetch_timeout_sec: float = 10.0,
) -> int:
    missing = [path for path in _required_files(assets_root) if not path.is_file()]
    if missing:
        for path in missing:
            print(f"MISSING: {path}", file=sys.stderr)
        return 2
    try:
        manifest = json.loads((assets_root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        print(f"INVALID MANIFEST: {exc}", file=sys.stderr)
        return 3
    sync_payload: dict[str, object] | None = None
    if sync:
        try:
            sync_payload = _sync_queries(
                assets_root=assets_root,
                official_root=official_root,
                nvim_root=nvim_root,
                timeout_sec=fetch_timeout_sec,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 4
    if lock_path is not None:
        payload = {
            "version": manifest.get("version", "unknown"),
            "assets_root": str(assets_root),
            "status": "synced" if sync_payload is not None else "validated",
        }
        if sync_payload is not None:
            payload["sync"] = sync_payload
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate/sync L3 query assets")
    parser.add_argument(
        "--assets-root",
        default="src/sari/services/collection/assets",
        help="Path to L3 assets root",
    )
    parser.add_argument(
        "--lock-path",
        default="src/sari/services/collection/assets/manifest.lock.json",
        help="Path to write lock metadata",
    )
    parser.add_argument("--check-only", action="store_true", help="Validate without writing lock file")
    parser.add_argument("--sync", action="store_true", help="Fetch and merge official+nvim query assets")
    parser.add_argument("--official-root", default="", help="Optional local root for official tags.scm fixtures")
    parser.add_argument("--nvim-root", default="", help="Optional local root for nvim tags.scm fixtures")
    parser.add_argument("--fetch-timeout-sec", default=10.0, type=float, help="Network fetch timeout")
    args = parser.parse_args(argv)
    root = Path(args.assets_root).resolve()
    official_root = Path(args.official_root).resolve() if str(args.official_root).strip() != "" else None
    nvim_root = Path(args.nvim_root).resolve() if str(args.nvim_root).strip() != "" else None
    lock_path = None if args.check_only else Path(args.lock_path).resolve()
    return run(
        assets_root=root,
        lock_path=lock_path,
        sync=bool(args.sync),
        official_root=official_root,
        nvim_root=nvim_root,
        fetch_timeout_sec=float(args.fetch_timeout_sec),
    )


if __name__ == "__main__":
    raise SystemExit(main())
