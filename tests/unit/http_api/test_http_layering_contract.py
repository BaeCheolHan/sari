"""HTTP 계층 레이어링 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path


def test_http_app_does_not_import_db_repositories_directly() -> None:
    """http/app.py는 db.repositories에 직접 의존하면 안 된다."""
    app_path = Path(__file__).resolve().parents[3] / "src" / "sari" / "http" / "app.py"
    source = app_path.read_text(encoding="utf-8")
    assert "from sari.db.repositories" not in source


def test_http_context_does_not_import_db_repositories_directly() -> None:
    """http/context.py는 db.repositories에 직접 의존하면 안 된다."""
    context_path = Path(__file__).resolve().parents[3] / "src" / "sari" / "http" / "context.py"
    source = context_path.read_text(encoding="utf-8")
    assert "from sari.db.repositories" not in source


def test_http_app_uses_dedicated_middleware_module() -> None:
    """http/app.py는 미들웨어 구현 세부를 직접 담지 않는다."""
    app_path = Path(__file__).resolve().parents[3] / "src" / "sari" / "http" / "app.py"
    source = app_path.read_text(encoding="utf-8")
    assert "from sari.http.middleware import" in source
    assert "class RuntimeSessionMiddleware" not in source
    assert "class BackgroundProxyMiddleware" not in source
