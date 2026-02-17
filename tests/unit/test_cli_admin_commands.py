"""CLI 운영 명령의 출력 계약을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from click.testing import CliRunner
from pytest import MonkeyPatch

from sari.cli.main import cli


def _prepare_home(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """CLI 기본 설정 경로가 임시 디렉터리를 사용하도록 HOME을 설정한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".local" / "share" / "sari-v2").mkdir(parents=True, exist_ok=True)


def _load_cli_schema() -> dict[str, object]:
    """CLI 출력 스키마 파일을 읽어 파싱한다."""
    schema_path = Path(__file__).resolve().parents[2] / "docs" / "cli_output_schema.json"
    return cast(dict[str, object], json.loads(schema_path.read_text(encoding="utf-8")))


def _resolve_ref(schema_root: dict[str, object], ref: str) -> dict[str, object]:
    """$ref 경로를 루트 스키마에서 해석한다."""
    if not ref.startswith("#/$defs/"):
        raise AssertionError(f"지원하지 않는 ref 형식: {ref}")
    def_name = ref.split("/", 2)[2]
    defs = cast(dict[str, object], schema_root["$defs"])
    target = defs.get(def_name)
    assert isinstance(target, dict)
    return cast(dict[str, object], target)


def _assert_schema(payload: object, schema: dict[str, object], schema_root: dict[str, object]) -> None:
    """간단한 JSON Schema 핵심 규칙(type/required/properties/items)을 검증한다."""
    ref = schema.get("$ref")
    if isinstance(ref, str):
        _assert_schema(payload, _resolve_ref(schema_root, ref), schema_root)
        return

    expected_type = schema.get("type")
    if expected_type == "object":
        assert isinstance(payload, dict)
        payload_obj = cast(dict[str, object], payload)
        required_keys = schema.get("required", [])
        assert isinstance(required_keys, list)
        for key in required_keys:
            assert isinstance(key, str)
            assert key in payload_obj
        properties = schema.get("properties", {})
        assert isinstance(properties, dict)
        for key, sub_schema in properties.items():
            if key not in payload_obj:
                continue
            assert isinstance(sub_schema, dict)
            _assert_schema(payload_obj[key], cast(dict[str, object], sub_schema), schema_root)
        return

    if expected_type == "array":
        assert isinstance(payload, list)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in payload:
                _assert_schema(item, cast(dict[str, object], item_schema), schema_root)
        return

    if expected_type == "string":
        assert isinstance(payload, str)
        return

    if expected_type == "integer":
        assert isinstance(payload, int)
        return

    if expected_type == "boolean":
        assert isinstance(payload, bool)
        return

    if expected_type == "null":
        assert payload is None
        return


def test_cli_doctor_command_outputs_checks(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """doctor 명령은 checks 배열을 포함한 JSON을 출력해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    schema = _load_cli_schema()
    defs = cast(dict[str, object], schema["$defs"])
    doctor_schema = cast(dict[str, object], defs["doctor_output"])
    _assert_schema(payload, doctor_schema, schema)


def test_cli_engine_status_outputs_dependencies(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """engine status 명령은 dependencies 정보를 포함해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["engine", "status"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    schema = _load_cli_schema()
    defs = cast(dict[str, object], schema["$defs"])
    engine_status_schema = cast(dict[str, object], defs["engine_status_output"])
    _assert_schema(payload, engine_status_schema, schema)


def test_cli_index_output_matches_schema(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """index 명령은 스키마에 정의된 필드를 반환해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["index"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    schema = _load_cli_schema()
    defs = cast(dict[str, object], schema["$defs"])
    index_schema = cast(dict[str, object], defs["index_output"])
    _assert_schema(payload, index_schema, schema)


def test_cli_install_print_codemode_matches_schema(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """install --host codex --print 출력이 스키마와 일치해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["install", "--host", "codex", "--print"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    schema = _load_cli_schema()
    defs = cast(dict[str, object], schema["$defs"])
    install_schema = cast(dict[str, object], defs["install_output_codex"])
    _assert_schema(payload, install_schema, schema)


def test_cli_roots_add_invalid_path_returns_error_contract(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """roots add 실패 시 error.code/message 구조를 반환해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["roots", "add", str(tmp_path / "not-exists")])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "error" in payload
    error = payload["error"]
    assert isinstance(error, dict)
    assert isinstance(error.get("code"), str)
    assert isinstance(error.get("message"), str)


def test_cli_pipeline_policy_show_outputs_policy_fields(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """pipeline policy show는 정책 필드를 포함한 JSON을 출력해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["pipeline", "policy", "show"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "policy" in payload
    policy = payload["policy"]
    assert isinstance(policy, dict)
    assert isinstance(policy.get("deletion_hold"), bool)
    assert isinstance(policy.get("l3_p95_threshold_ms"), int)


def test_cli_pipeline_auto_set_and_status(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """pipeline auto set/status 명령은 자동제어 상태를 반영해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    set_result = runner.invoke(cli, ["pipeline", "auto", "set", "--enabled", "on"])
    assert set_result.exit_code == 0
    set_payload = json.loads(set_result.output)
    assert set_payload["auto_control"]["auto_hold_enabled"] is True

    status_result = runner.invoke(cli, ["pipeline", "auto", "status"])
    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.output)
    assert status_payload["auto_control"]["auto_hold_enabled"] is True
