"""Approved-folder boundary enforcement.

Every file tool resolves its path through ``ApprovedRoots.resolve`` before
touching the filesystem. Resolution expands the user directory, resolves
symlinks (so a link inside a root cannot escape it), and requires the result
to sit inside one of the user-approved roots. Anything else raises.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path


class PathOutsideRootsError(Exception):
    pass


class ApprovedRoots:
    def __init__(self, get_roots: Callable[[], list[str]]):
        self._get_roots = get_roots

    def roots(self) -> list[Path]:
        return [Path(r).expanduser().resolve() for r in self._get_roots()]

    def resolve(self, raw_path: str, for_write: bool = False) -> Path:
        """Return a safe absolute path inside an approved root, or raise.

        For writes the target may not exist yet, so the deepest *existing*
        ancestor is what gets symlink-resolved and boundary-checked.
        """
        roots = self.roots()
        if not roots:
            raise PathOutsideRootsError(
                "No approved folders. Add one in the Permission Center first."
            )

        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            # Relative paths resolve against the first approved root.
            candidate = roots[0] / candidate

        if for_write and not candidate.exists():
            resolved_parent = candidate.parent.resolve()
            resolved = resolved_parent / candidate.name
        else:
            resolved = candidate.resolve()

        for root in roots:
            try:
                if os.path.commonpath([resolved, root]) == str(root):
                    return resolved
            except ValueError:
                continue  # different drives / invalid mix
        raise PathOutsideRootsError(f"Path is outside the approved folders: {raw_path}")
