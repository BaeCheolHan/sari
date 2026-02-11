from sari.core.utils.path import PathUtils


def test_normalize_windows_drive_letter_is_case_insensitive():
    p1 = PathUtils.normalize(r"C:\Project\file.txt")
    p2 = PathUtils.normalize(r"c:\Project\file.txt")
    assert p1 == p2
