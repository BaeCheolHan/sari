"""런타임 설정 facade.

실제 구현은 `config_model.py`에 두고, 기존 import 경로(`sari.core.config`)는
호환을 위해 유지한다.
"""

from sari.core.config_model import (
    AppConfig,
    CollectionRuntimeConfigDTO,
    DEFAULT_COLLECTION_EXCLUDE_GLOBS,
    LspHubRuntimeConfigDTO,
    SearchRuntimeConfigDTO,
)

__all__ = [
    "DEFAULT_COLLECTION_EXCLUDE_GLOBS",
    "CollectionRuntimeConfigDTO",
    "LspHubRuntimeConfigDTO",
    "SearchRuntimeConfigDTO",
    "AppConfig",
]
