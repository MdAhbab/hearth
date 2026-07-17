"""Approved-folder boundary: traversal, symlink escape, relative paths."""

import pytest

from hearth.connectors.files.roots import ApprovedRoots, PathOutsideRootsError


@pytest.fixture
def roots(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    (tmp_path / "outside").mkdir()
    return ApprovedRoots(lambda: [str(approved)]), tmp_path


def test_inside_root_ok(roots):
    ar, tmp = roots
    target = tmp / "approved" / "note.txt"
    target.write_text("hi")
    assert ar.resolve(str(target)) == target


def test_dotdot_traversal_rejected(roots):
    ar, tmp = roots
    with pytest.raises(PathOutsideRootsError):
        ar.resolve(str(tmp / "approved" / ".." / "outside" / "x.txt"))


def test_absolute_outside_rejected(roots):
    ar, tmp = roots
    with pytest.raises(PathOutsideRootsError):
        ar.resolve(str(tmp / "outside" / "x.txt"))


def test_symlink_escape_rejected(roots):
    ar, tmp = roots
    escape = tmp / "approved" / "sneaky"
    escape.symlink_to(tmp / "outside")
    with pytest.raises(PathOutsideRootsError):
        ar.resolve(str(escape / "x.txt"))


def test_relative_path_resolves_into_first_root(roots):
    ar, tmp = roots
    resolved = ar.resolve("sub/file.txt", for_write=True)
    assert str(resolved).startswith(str(tmp / "approved"))


def test_write_target_may_not_exist(roots):
    ar, tmp = roots
    resolved = ar.resolve(str(tmp / "approved" / "new" / "file.txt"), for_write=True)
    assert resolved.name == "file.txt"


def test_no_roots_rejects_everything(tmp_path):
    ar = ApprovedRoots(lambda: [])
    with pytest.raises(PathOutsideRootsError):
        ar.resolve(str(tmp_path / "anything.txt"))


def test_prefix_sibling_rejected(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data-evil").mkdir()
    ar = ApprovedRoots(lambda: [str(tmp_path / "data")])
    with pytest.raises(PathOutsideRootsError):
        ar.resolve(str(tmp_path / "data-evil" / "x.txt"))
