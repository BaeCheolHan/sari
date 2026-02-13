from typing import List


def _cmd_uninstall(argv: List[str]) -> int:
    from sari import uninstall as uninstall_mod

    return uninstall_mod.main(argv[1:])
