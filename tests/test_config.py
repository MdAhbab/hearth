"""Config round-trip: every section (including [user] and cloud-primary
model choices) survives save -> load."""

from __future__ import annotations

from hearth.config import Config


def test_defaults_load_without_file(tmp_path):
    config = Config.load(tmp_path / "missing.toml")
    assert config.model.provider == "ollama"
    assert config.user.name == ""


def test_round_trip_preserves_user_and_model_choice(tmp_path):
    path = tmp_path / "config.toml"
    config = Config()
    config.model.provider = "gemini"
    config.model.name = "gemini-2.5-flash"
    config.user.name = "Ada"
    config.user.about = 'Writes "code" \\ prose'
    config.fallback.enabled = True
    config.save(path)

    loaded = Config.load(path)
    assert loaded.model.provider == "gemini"
    assert loaded.model.name == "gemini-2.5-flash"
    assert loaded.user.name == "Ada"
    assert loaded.user.about == 'Writes "code" \\ prose'
    assert loaded.fallback.enabled is True


def test_round_trip_keeps_defaults_stable(tmp_path):
    path = tmp_path / "config.toml"
    Config().save(path)
    loaded = Config.load(path)
    assert loaded == Config()
