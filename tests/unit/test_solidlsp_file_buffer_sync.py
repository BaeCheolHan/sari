"""LSPFileBuffer 증분 동기화 정책을 검증한다."""

from solidlsp.ls import LSPFileBuffer


class _NotifyDouble:
    """did* 알림 호출을 기록하는 테스트 더블이다."""

    def __init__(self) -> None:
        self.change_calls: list[dict[str, object]] = []

    def did_open_text_document(self, payload: dict[str, object]) -> None:
        del payload

    def did_close_text_document(self, payload: dict[str, object]) -> None:
        del payload

    def did_change_text_document(self, payload: dict[str, object]) -> None:
        self.change_calls.append(payload)


class _ServerDouble:
    """notify 인터페이스를 제공하는 테스트 더블이다."""

    def __init__(self) -> None:
        self.notify = _NotifyDouble()


class _LanguageServerDouble:
    """LSPFileBuffer가 참조하는 language_server 래퍼다."""

    def __init__(self) -> None:
        self.server = _ServerDouble()


def _new_buffer() -> tuple[LSPFileBuffer, _NotifyDouble]:
    """테스트용 버퍼와 notify 더블을 구성한다."""
    language_server = _LanguageServerDouble()
    buffer = LSPFileBuffer(
        uri="file:///tmp/sample.py",
        contents="print('a')",
        encoding="utf-8",
        version=0,
        language_id="python",
        ref_count=1,
        language_server=language_server,
        open_in_ls=False,
    )
    buffer.ensure_open_in_ls()
    return buffer, language_server.server.notify


def test_sync_changes_to_ls_sends_full_text_when_hash_is_dirty() -> None:
    """해시가 달라지면 full-text didChange를 전송해야 한다."""
    buffer, notify = _new_buffer()
    buffer.contents = "print('b')"
    buffer.mark_content_updated()

    buffer.sync_changes_to_ls()

    assert len(notify.change_calls) == 1


def test_mark_incremental_change_synced_skips_redundant_full_sync() -> None:
    """증분 변경이 이미 반영됐으면 full-text didChange를 중복 전송하지 않아야 한다."""
    buffer, notify = _new_buffer()
    buffer.contents = "print('b')"
    buffer.mark_content_updated()
    buffer.mark_incremental_change_synced()

    buffer.sync_changes_to_ls()

    assert len(notify.change_calls) == 0
