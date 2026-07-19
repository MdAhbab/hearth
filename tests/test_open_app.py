"""Windows open-app: Start Menu shortcut matching (pure logic, any platform)."""

from __future__ import annotations

from hearth.connectors.system.tools import find_start_menu_apps


def _make_lnk(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.lnk"
    path.write_bytes(b"")
    return path


def test_exact_match_wins(tmp_path):
    programs = tmp_path / "Programs"
    _make_lnk(programs, "Google Chrome")
    _make_lnk(programs, "Google Chrome Canary")
    matches = find_start_menu_apps("google chrome", dirs=[programs])
    assert matches[0].stem == "Google Chrome"
    assert len(matches) == 2  # canary offered as an alternative


def test_substring_match(tmp_path):
    programs = tmp_path / "Programs"
    _make_lnk(programs, "Mozilla Firefox")
    matches = find_start_menu_apps("firefox", dirs=[programs])
    assert [m.stem for m in matches] == ["Mozilla Firefox"]


def test_no_match_and_blank_query(tmp_path):
    programs = tmp_path / "Programs"
    _make_lnk(programs, "Notepad")
    assert find_start_menu_apps("blender", dirs=[programs]) == []
    assert find_start_menu_apps("   ", dirs=[programs]) == []


def test_duplicate_across_menus_collapsed(tmp_path):
    user = tmp_path / "user"
    system = tmp_path / "system"
    _make_lnk(user, "Notepad")
    _make_lnk(system, "Notepad")
    matches = find_start_menu_apps("notepad", dirs=[user, system])
    assert len(matches) == 1


def test_searches_subdirectories_and_missing_dirs(tmp_path):
    programs = tmp_path / "Programs"
    _make_lnk(programs / "Accessories", "Paint")
    missing = tmp_path / "does-not-exist"
    matches = find_start_menu_apps("paint", dirs=[missing, programs])
    assert [m.stem for m in matches] == ["Paint"]
