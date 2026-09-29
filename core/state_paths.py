"""Conservative directory checks for state cleanup boundaries."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def unlinked_directory_tree(path: Path, *, allow_missing: bool = False) -> bool:
    """Reject links and Windows reparse points at every directory component."""

    current = path.absolute()
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not allow_missing:
                return False
            if current.parent == current:
                return False
            current = current.parent
            continue
        except OSError:
            return False
        if not stat.S_ISDIR(info.st_mode) or (
            os.name == "nt" and info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            return False
        if current.parent == current:
            return True
        current = current.parent
