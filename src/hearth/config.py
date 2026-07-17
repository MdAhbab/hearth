"""Typed application configuration loaded from a TOML file.

The config file lives in the per-user data directory (platform-appropriate:
~/Library/Application Support/Hearth on macOS, %LOCALAPPDATA%\\Hearth on
Windows, ~/.local/share/Hearth on Linux). Every field has a safe default;
a missing config file is not an error.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from platformdirs import user_data_dir, user_log_dir
from pydantic import BaseModel, Field

APP_NAME = "Hearth"


def app_data_dir() -> Path:
    return Path(user_data_dir(APP_NAME, appauthor=False))


def app_log_dir() -> Path:
    return Path(user_log_dir(APP_NAME, appauthor=False))


def default_config_path() -> Path:
    return app_data_dir() / "config.toml"


class ModelConfig(BaseModel):
    provider: str = "ollama"
    name: str = "gemma4:e2b"
    context_length: int = 4096
    max_agent_steps: int = 6
    keep_alive: str = "5m"


class OllamaConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 11434
    autostart: bool = True
    startup_timeout_s: float = 20.0
    request_timeout_s: float = 120.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class GmailConfig(BaseModel):
    credentials_file: str = ""


class CalendarConfig(BaseModel):
    # "auto" = EventKit on macOS, Google Calendar elsewhere.
    backend: str = "auto"


class FilesConfig(BaseModel):
    max_read_bytes: int = 262_144


class WebConfig(BaseModel):
    max_fetch_bytes: int = 500_000
    fetch_timeout_s: float = 20.0


class Config(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    gmail: GmailConfig = Field(default_factory=GmailConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)
    web: WebConfig = Field(default_factory=WebConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or default_config_path()
        if not path.exists():
            return cls()
        with path.open("rb") as f:
            data = tomllib.load(f)
        return cls.model_validate(data)

    def save(self, path: Path | None = None) -> None:
        """Persist the current config as TOML (simple flat emitter)."""
        path = path or default_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for section, model in (
            ("model", self.model),
            ("ollama", self.ollama),
            ("gmail", self.gmail),
            ("calendar", self.calendar),
            ("files", self.files),
            ("web", self.web),
        ):
            lines.append(f"[{section}]")
            for key, value in model.model_dump().items():
                if isinstance(value, bool):
                    lines.append(f"{key} = {'true' if value else 'false'}")
                elif isinstance(value, (int, float)):
                    lines.append(f"{key} = {value}")
                else:
                    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'{key} = "{escaped}"')
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
