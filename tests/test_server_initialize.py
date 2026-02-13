from __future__ import annotations

import pytest

from sari.mcp.server_initialize import (
    build_initialize_result,
    choose_target_uri,
    iter_client_protocol_versions,
    negotiate_protocol_version,
)


def test_choose_target_uri_prefers_root_uri_then_workspace_folders():
    assert choose_target_uri({"rootUri": "file:///a"}) == "file:///a"
    assert choose_target_uri({"rootPath": "/tmp/a"}) == "/tmp/a"
    assert choose_target_uri({"workspaceFolders": [{"uri": "file:///b"}]}) == "file:///b"
    assert choose_target_uri({"workspaceFolders": ["bad-entry"]}) is None


def test_iter_client_protocol_versions_deduplicates_and_trims():
    params = {
        "protocolVersion": " 2025-11-25 ",
        "supportedProtocolVersions": ["2025-11-25", "2025-06-18", "", None],
        "capabilities": {"protocolVersions": ["2025-03-26", "2025-06-18"]},
    }
    assert iter_client_protocol_versions(params) == [
        "2025-11-25",
        "2025-06-18",
        "2025-03-26",
    ]


def test_negotiate_protocol_version_uses_first_supported_or_default():
    params = {"supportedProtocolVersions": ["9999-99-99", "2025-06-18"]}
    picked = negotiate_protocol_version(
        params=params,
        supported_versions={"2025-06-18", "2025-11-25"},
        default_version="2025-11-25",
        strict_protocol=False,
    )
    assert picked == "2025-06-18"

    fallback = negotiate_protocol_version(
        params={"supportedProtocolVersions": ["9999-99-99"]},
        supported_versions={"2025-06-18", "2025-11-25"},
        default_version="2025-11-25",
        strict_protocol=False,
    )
    assert fallback == "2025-11-25"


def test_negotiate_protocol_version_strict_raises_with_error_builder():
    def _error_builder(supported):
        return RuntimeError(f"unsupported:{','.join(supported)}")

    with pytest.raises(RuntimeError):
        negotiate_protocol_version(
            params={"supportedProtocolVersions": ["9999-99-99"]},
            supported_versions={"2025-06-18", "2025-11-25"},
            default_version="2025-11-25",
            strict_protocol=True,
            error_builder=_error_builder,
        )


def test_build_initialize_result_has_expected_capability_shape():
    result = build_initialize_result("2025-11-25", "sari", "1.2.3")
    assert result["protocolVersion"] == "2025-11-25"
    assert result["serverInfo"]["name"] == "sari"
    assert result["capabilities"]["tools"] == {"listChanged": False}
