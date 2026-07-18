"""Skill library: builtins, /command expansion, user overrides, bad files."""

from hearth.skills import SkillLibrary


def make_library(tmp_path, files: dict[str, str] | None = None) -> SkillLibrary:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    for name, content in (files or {}).items():
        (skills_dir / name).write_text(content, encoding="utf-8")
    return SkillLibrary(skills_dir=skills_dir)


def test_builtins_present(tmp_path):
    library = make_library(tmp_path)
    assert {"today", "inbox", "focus", "tidy"} <= set(library.names())


def test_expand_builtin(tmp_path):
    library = make_library(tmp_path)
    expanded = library.expand("/inbox")
    assert expanded and "is:unread" in expanded


def test_expand_with_input(tmp_path):
    library = make_library(tmp_path)
    expanded = library.expand("/tidy ~/Documents/notes")
    assert expanded and "~/Documents/notes" in expanded


def test_unknown_command_returns_none(tmp_path):
    library = make_library(tmp_path)
    assert library.expand("/definitely-not-a-skill") is None
    assert library.expand("plain message") is None


def test_user_skill_and_override(tmp_path):
    library = make_library(
        tmp_path,
        {
            "weekly.md": "# weekly — plan my week\nPlan my week. {input}",
            "inbox.md": "# inbox — custom inbox\nMy own inbox prompt.",
        },
    )
    assert library.expand("/weekly go easy") == "Plan my week. go easy"
    assert library.expand("/inbox") == "My own inbox prompt."  # override wins


def test_malformed_files_ignored(tmp_path):
    library = make_library(
        tmp_path,
        {
            "empty.md": "",
            "noheader.md": "just text without a header",
            "nobody.md": "# nobody — description only",
        },
    )
    for name in ("empty", "noheader", "nobody"):
        assert library.get(name) is None
    assert "today" in library.names()  # builtins unaffected


def test_help_text_lists_commands(tmp_path):
    library = make_library(tmp_path)
    text = library.help_text()
    assert "/today" in text and "/inbox" in text
