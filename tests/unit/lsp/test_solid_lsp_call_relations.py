from __future__ import annotations

from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException

from sari.services.collection.l5.solid_lsp_extraction_backend import SolidLspExtractionBackend


def test_extract_once_builds_relations_from_referencing_symbols_same_file() -> None:
    backend = SolidLspExtractionBackend(hub=object())  # type: ignore[arg-type]
    try:
        raw_symbols = [
            {
                "name": "callee_fn",
                "kind": "function",
                "location": {
                    "selectionRange": {
                        "start": {"line": 10, "character": 4},
                        "end": {"line": 10, "character": 13},
                    },
                    "range": {
                        "start": {"line": 10, "character": 0},
                        "end": {"line": 20, "character": 1},
                    },
                },
            }
        ]

        class _FakeLsp:
            def request_referencing_symbols(self, *args, **kwargs):  # noqa: ANN002, ANN003
                _ = (args, kwargs)
                return [
                    {
                        "symbol": {
                            "name": "caller_fn",
                            "location": {"relativePath": "src/a.py"},
                        },
                        "line": 15,
                    }
                ]

        backend._extract_request_runner_service.run_request = (  # type: ignore[method-assign]
            lambda **kwargs: (Language.PYTHON, raw_symbols, _FakeLsp(), "src/a.py")
        )
        backend._symbol_normalizer_service.normalize_symbols = (  # type: ignore[method-assign]
            lambda **kwargs: [
                {
                    "name": "callee_fn",
                    "kind": "function",
                    "line": 10,
                    "end_line": 20,
                    "symbol_key": None,
                    "parent_symbol_key": None,
                    "depth": 0,
                    "container_name": None,
                }
            ]
        )

        result = backend._extract_once(repo_root="/repo", normalized_relative_path="src/a.py")  # noqa: SLF001
        assert result.error_message is None
        assert result.relations == [
            {
                "from_symbol": "caller_fn",
                "to_symbol": "callee_fn",
                "line": 15,
                "caller_relative_path": "src/a.py",
            }
        ]
    finally:
        backend.shutdown_probe_executor()


def test_extract_once_keeps_cross_file_relations_with_caller_path() -> None:
    backend = SolidLspExtractionBackend(hub=object())  # type: ignore[arg-type]
    try:
        raw_symbols = [
            {
                "name": "callee_fn",
                "kind": "function",
                "location": {
                    "selectionRange": {
                        "start": {"line": 10, "character": 4},
                        "end": {"line": 10, "character": 13},
                    }
                },
            }
        ]

        class _FakeLsp:
            def request_referencing_symbols(self, *args, **kwargs):  # noqa: ANN002, ANN003
                _ = (args, kwargs)
                return [
                    {
                        "symbol": {
                            "name": "external_caller",
                            "location": {"relativePath": "src/other.py"},
                        },
                        "line": 3,
                    }
                ]

        backend._extract_request_runner_service.run_request = (  # type: ignore[method-assign]
            lambda **kwargs: (Language.PYTHON, raw_symbols, _FakeLsp(), "src/a.py")
        )
        backend._symbol_normalizer_service.normalize_symbols = (  # type: ignore[method-assign]
            lambda **kwargs: [
                {
                    "name": "callee_fn",
                    "kind": "function",
                    "line": 10,
                    "end_line": 20,
                    "symbol_key": None,
                    "parent_symbol_key": None,
                    "depth": 0,
                    "container_name": None,
                }
            ]
        )

        result = backend._extract_once(repo_root="/repo", normalized_relative_path="src/a.py")  # noqa: SLF001
        assert result.error_message is None
        assert result.relations == [
            {
                "from_symbol": "external_caller",
                "to_symbol": "callee_fn",
                "line": 3,
                "caller_relative_path": "src/other.py",
            }
        ]
    finally:
        backend.shutdown_probe_executor()


def test_extract_once_uses_selection_position_for_reference_lookup_with_declaration_key() -> None:
    backend = SolidLspExtractionBackend(hub=object())  # type: ignore[arg-type]
    try:
        raw_symbols = [
            {
                "name": "decorated_fn",
                "kind": "function",
                "selectionRange": {
                    "start": {"line": 21, "character": 8},
                    "end": {"line": 21, "character": 20},
                },
                "location": {
                    "range": {
                        "start": {"line": 20, "character": 0},
                        "end": {"line": 30, "character": 1},
                    },
                },
            }
        ]
        captured: dict[str, int] = {}

        class _FakeLsp:
            def request_referencing_symbols(self, relative_path, line, column, **kwargs):  # noqa: ANN001
                _ = kwargs
                captured["line"] = int(line)
                captured["column"] = int(column)
                captured["path_len"] = len(str(relative_path))
                if int(line) == 21 and int(column) == 8:
                    return [{"symbol": {"name": "caller_fn", "location": {"relativePath": "src/a.py"}}, "line": 25}]
                return []

        backend._extract_request_runner_service.run_request = (  # type: ignore[method-assign]
            lambda **kwargs: (Language.PYTHON, raw_symbols, _FakeLsp(), "src/a.py")
        )
        backend._symbol_normalizer_service.normalize_symbols = (  # type: ignore[method-assign]
            lambda **kwargs: [
                {
                    "name": "decorated_fn",
                    "kind": "function",
                    "line": 20,
                    "end_line": 30,
                    "symbol_key": None,
                    "parent_symbol_key": None,
                    "depth": 0,
                    "container_name": None,
                }
            ]
        )

        result = backend._extract_once(repo_root="/repo", normalized_relative_path="src/a.py")  # noqa: SLF001
        assert result.error_message is None
        assert captured["line"] == 21
        assert captured["column"] == 8
        assert result.relations == [
            {
                "from_symbol": "caller_fn",
                "to_symbol": "decorated_fn",
                "line": 25,
                "caller_relative_path": "src/a.py",
            }
        ]
    finally:
        backend.shutdown_probe_executor()


def test_extract_once_falls_back_to_symbol_name_column_when_selection_range_missing() -> None:
    backend = SolidLspExtractionBackend(hub=object())  # type: ignore[arg-type]
    try:
        raw_symbols = [
            {
                "name": "CommonMessageService",
                "kind": "class",
                "location": {
                    "range": {
                        "start": {"line": 1, "character": 0},
                        "end": {"line": 5, "character": 1},
                    },
                },
            }
        ]
        captured: dict[str, int] = {}

        class _FakeLsp:
            def request_referencing_symbols(self, relative_path, line, column, **kwargs):  # noqa: ANN001
                _ = (relative_path, kwargs)
                captured["line"] = int(line)
                captured["column"] = int(column)
                if int(column) == 13:
                    return [{"symbol": {"name": "caller_fn", "location": {"relativePath": "src/other.py"}}, "line": 11}]
                return []

        backend._extract_request_runner_service.run_request = (  # type: ignore[method-assign]
            lambda **kwargs: (Language.PYTHON, raw_symbols, _FakeLsp(), "src/a.py")
        )
        backend._symbol_normalizer_service.normalize_symbols = (  # type: ignore[method-assign]
            lambda **kwargs: [
                {
                    "name": "CommonMessageService",
                    "kind": "class",
                    "line": 1,
                    "end_line": 5,
                    "symbol_key": None,
                    "parent_symbol_key": None,
                    "depth": 0,
                    "container_name": None,
                }
            ]
        )
        backend._load_source_lines = lambda **kwargs: [  # type: ignore[method-assign]
            "package x;",
            "public class CommonMessageService {",
            "}",
        ]

        result = backend._extract_once(repo_root="/repo", normalized_relative_path="src/a.py")  # noqa: SLF001
        assert result.error_message is None
        assert captured["line"] == 1
        assert captured["column"] == 13
        assert result.relations == [
            {
                "from_symbol": "caller_fn",
                "to_symbol": "CommonMessageService",
                "line": 11,
                "caller_relative_path": "src/other.py",
            }
        ]
    finally:
        backend.shutdown_probe_executor()


def test_extract_once_rejects_reference_without_location_evidence() -> None:
    backend = SolidLspExtractionBackend(hub=object())  # type: ignore[arg-type]
    try:
        raw_symbols = [
            {
                "name": "callee_fn",
                "kind": "function",
                "location": {
                    "selectionRange": {
                        "start": {"line": 10, "character": 4},
                        "end": {"line": 10, "character": 13},
                    },
                    "range": {
                        "start": {"line": 10, "character": 0},
                        "end": {"line": 20, "character": 1},
                    },
                },
            }
        ]

        class _FakeLsp:
            def request_referencing_symbols(self, *args, **kwargs):  # noqa: ANN002, ANN003
                _ = (args, kwargs)
                return [
                    {
                        "symbol": {
                            "name": "caller_without_location",
                        },
                        "line": 15,
                    }
                ]

        backend._extract_request_runner_service.run_request = (  # type: ignore[method-assign]
            lambda **kwargs: (Language.PYTHON, raw_symbols, _FakeLsp(), "src/a.py")
        )
        backend._symbol_normalizer_service.normalize_symbols = (  # type: ignore[method-assign]
            lambda **kwargs: [
                {
                    "name": "callee_fn",
                    "kind": "function",
                    "line": 10,
                    "end_line": 20,
                    "symbol_key": None,
                    "parent_symbol_key": None,
                    "depth": 0,
                    "container_name": None,
                }
            ]
        )

        result = backend._extract_once(repo_root="/repo", normalized_relative_path="src/a.py")  # noqa: SLF001
        assert result.error_message is None
        assert result.relations == []
    finally:
        backend.shutdown_probe_executor()


def test_extract_once_ignores_reference_query_solidlsp_exception() -> None:
    backend = SolidLspExtractionBackend(hub=object())  # type: ignore[arg-type]
    try:
        raw_symbols = [
            {
                "name": "callee_fn",
                "kind": "function",
                "location": {
                    "selectionRange": {
                        "start": {"line": 10, "character": 4},
                        "end": {"line": 10, "character": 13},
                    },
                    "range": {
                        "start": {"line": 10, "character": 0},
                        "end": {"line": 20, "character": 1},
                    },
                },
            }
        ]

        class _FakeLsp:
            def request_referencing_symbols(self, *args, **kwargs):  # noqa: ANN002, ANN003
                _ = (args, kwargs)
                raise SolidLSPException("temporary reference request failure")

        backend._extract_request_runner_service.run_request = (  # type: ignore[method-assign]
            lambda **kwargs: (Language.PYTHON, raw_symbols, _FakeLsp(), "src/a.py")
        )
        backend._symbol_normalizer_service.normalize_symbols = (  # type: ignore[method-assign]
            lambda **kwargs: [
                {
                    "name": "callee_fn",
                    "kind": "function",
                    "line": 10,
                    "end_line": 20,
                    "symbol_key": None,
                    "parent_symbol_key": None,
                    "depth": 0,
                    "container_name": None,
                }
            ]
        )

        result = backend._extract_once(repo_root="/repo", normalized_relative_path="src/a.py")  # noqa: SLF001
        assert result.error_message is None
        assert result.relations == []
    finally:
        backend.shutdown_probe_executor()


def test_extract_once_accepts_reference_dataclass_objects() -> None:
    backend = SolidLspExtractionBackend(hub=object())  # type: ignore[arg-type]
    try:
        raw_symbols = [
            {
                "name": "callee_fn",
                "kind": "function",
                "location": {
                    "selectionRange": {
                        "start": {"line": 10, "character": 4},
                        "end": {"line": 10, "character": 13},
                    },
                    "range": {
                        "start": {"line": 10, "character": 0},
                        "end": {"line": 20, "character": 1},
                    },
                },
            }
        ]

        class _Ref:
            def __init__(self, symbol: dict[str, object], line: int) -> None:
                self.symbol = symbol
                self.line = line

        class _FakeLsp:
            def request_referencing_symbols(self, *args, **kwargs):  # noqa: ANN002, ANN003
                _ = (args, kwargs)
                return [_Ref({"name": "caller_obj", "location": {"relativePath": "src/a.py"}}, 15)]

        backend._extract_request_runner_service.run_request = (  # type: ignore[method-assign]
            lambda **kwargs: (Language.PYTHON, raw_symbols, _FakeLsp(), "src/a.py")
        )
        backend._symbol_normalizer_service.normalize_symbols = (  # type: ignore[method-assign]
            lambda **kwargs: [
                {
                    "name": "callee_fn",
                    "kind": "function",
                    "line": 10,
                    "end_line": 20,
                    "symbol_key": None,
                    "parent_symbol_key": None,
                    "depth": 0,
                    "container_name": None,
                }
            ]
        )

        result = backend._extract_once(repo_root="/repo", normalized_relative_path="src/a.py")  # noqa: SLF001
        assert result.error_message is None
        assert result.relations == [
            {
                "from_symbol": "caller_obj",
                "to_symbol": "callee_fn",
                "line": 15,
                "caller_relative_path": "src/a.py",
            }
        ]
    finally:
        backend.shutdown_probe_executor()
