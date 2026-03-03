"""LSP 언어/확장자 SSOT 레지스트리를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from solidlsp.ls_config import Language


@dataclass(frozen=True)
class LanguageSupportEntry:
    """운영에서 활성화할 언어와 확장자 집합을 표현한다."""

    language: Language
    extensions: tuple[str, ...]


_LANGUAGE_SUPPORT_ENTRIES: tuple[LanguageSupportEntry, ...] = (
    LanguageSupportEntry(language=Language.PYTHON, extensions=(".py", ".pyi")),
    LanguageSupportEntry(language=Language.TYPESCRIPT, extensions=(".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs")),
    LanguageSupportEntry(language=Language.JAVA, extensions=(".java",)),
    LanguageSupportEntry(language=Language.KOTLIN, extensions=(".kt", ".kts")),
    LanguageSupportEntry(language=Language.GO, extensions=(".go",)),
    LanguageSupportEntry(language=Language.RUST, extensions=(".rs",)),
    LanguageSupportEntry(language=Language.CSHARP, extensions=(".cs",)),
    LanguageSupportEntry(language=Language.RUBY, extensions=(".rb", ".erb")),
    LanguageSupportEntry(language=Language.DART, extensions=(".dart",)),
    LanguageSupportEntry(language=Language.CPP, extensions=(".cpp", ".cc", ".cxx", ".c", ".hpp", ".hxx", ".h")),
    LanguageSupportEntry(language=Language.PHP, extensions=(".php",)),
    LanguageSupportEntry(language=Language.R, extensions=(".r", ".rmd", ".rnw")),
    LanguageSupportEntry(language=Language.PERL, extensions=(".pl", ".pm", ".t")),
    LanguageSupportEntry(language=Language.CLOJURE, extensions=(".clj", ".cljs", ".cljc", ".edn")),
    LanguageSupportEntry(language=Language.ELIXIR, extensions=(".ex", ".exs")),
    LanguageSupportEntry(language=Language.ELM, extensions=(".elm",)),
    LanguageSupportEntry(language=Language.TERRAFORM, extensions=(".tf", ".tfvars", ".tfstate")),
    LanguageSupportEntry(language=Language.SWIFT, extensions=(".swift",)),
    LanguageSupportEntry(language=Language.BASH, extensions=(".sh", ".bash")),
    LanguageSupportEntry(language=Language.ZIG, extensions=(".zig", ".zon")),
    LanguageSupportEntry(language=Language.LUA, extensions=(".lua",)),
    LanguageSupportEntry(language=Language.NIX, extensions=(".nix",)),
    LanguageSupportEntry(language=Language.ERLANG, extensions=(".erl", ".hrl", ".escript")),
    LanguageSupportEntry(language=Language.AL, extensions=(".al", ".dal")),
    LanguageSupportEntry(language=Language.FSHARP, extensions=(".fs", ".fsx", ".fsi")),
    LanguageSupportEntry(language=Language.REGO, extensions=(".rego",)),
    LanguageSupportEntry(language=Language.SCALA, extensions=(".scala", ".sbt")),
    LanguageSupportEntry(language=Language.JULIA, extensions=(".jl",)),
    LanguageSupportEntry(language=Language.FORTRAN, extensions=(".f90", ".f95", ".f03", ".f08", ".f", ".for", ".fpp")),
    LanguageSupportEntry(language=Language.HASKELL, extensions=(".hs", ".lhs")),
    LanguageSupportEntry(language=Language.GROOVY, extensions=(".groovy", ".gvy")),
    LanguageSupportEntry(language=Language.VUE, extensions=(".vue",)),
    LanguageSupportEntry(language=Language.POWERSHELL, extensions=(".ps1", ".psm1", ".psd1")),
    LanguageSupportEntry(language=Language.PASCAL, extensions=(".pas", ".pp", ".lpr", ".dpr", ".dpk", ".inc")),
    LanguageSupportEntry(language=Language.MATLAB, extensions=(".m", ".mlx", ".mlapp")),
    LanguageSupportEntry(language=Language.MARKDOWN, extensions=(".md", ".markdown")),
    LanguageSupportEntry(language=Language.YAML, extensions=(".yaml", ".yml")),
    LanguageSupportEntry(language=Language.TOML, extensions=(".toml",)),
)

_CRITICAL_LANGUAGE_NAMES: tuple[str, ...] = (
    "python",
    "typescript",
    "java",
    "kotlin",
    "go",
    "rust",
    "csharp",
)


def iter_language_support_entries() -> tuple[LanguageSupportEntry, ...]:
    """활성 언어 엔트리를 반환한다."""
    return _LANGUAGE_SUPPORT_ENTRIES


def get_enabled_languages() -> tuple[Language, ...]:
    """운영 활성 언어 목록을 반환한다."""
    return tuple(entry.language for entry in _LANGUAGE_SUPPORT_ENTRIES)


def get_enabled_language_names() -> tuple[str, ...]:
    """운영 활성 언어 이름 목록을 반환한다."""
    return tuple(entry.language.value for entry in _LANGUAGE_SUPPORT_ENTRIES)


def get_critical_language_names() -> tuple[str, ...]:
    """하드 게이트에서 100% 성공이 필요한 핵심 언어 목록을 반환한다."""
    enabled = set(get_enabled_language_names())
    return tuple(name for name in _CRITICAL_LANGUAGE_NAMES if name in enabled)


def normalize_language_filter(raw_values: list[str] | tuple[str, ...] | None) -> tuple[str, ...] | None:
    """입력 언어 필터를 정규화한다."""
    if raw_values is None:
        return None
    normalized: list[str] = []
    for raw in raw_values:
        name = str(raw).strip().lower()
        if name == "":
            continue
        normalized.append(name)
    if len(normalized) == 0:
        return None
    enabled = set(get_enabled_language_names())
    invalid = [name for name in normalized if name not in enabled]
    if len(invalid) > 0:
        raise ValueError(f"unsupported language filter: {', '.join(sorted(set(invalid)))}")
    deduped = sorted(set(normalized))
    return tuple(deduped)


def get_default_collection_extensions() -> tuple[str, ...]:
    """수집 기본 확장자 목록을 반환한다."""
    extension_set: set[str] = set()
    for entry in _LANGUAGE_SUPPORT_ENTRIES:
        extension_set.update(entry.extensions)
    return tuple(sorted(extension_set))


def resolve_language_from_path(file_path: str) -> Language | None:
    """파일 경로 기반으로 활성 언어를 결정한다."""
    suffix = Path(file_path).suffix.lower()
    if suffix == "":
        return None
    for entry in _LANGUAGE_SUPPORT_ENTRIES:
        if suffix in entry.extensions:
            return entry.language
    return None
