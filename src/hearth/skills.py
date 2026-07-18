"""Skills: reusable prompt commands invoked with /name in the chat box.

A skill is a named prompt template. Built-ins cover common routines; users
add their own by dropping markdown files into <data dir>/skills/:

    # weekly — plan my week
    Look at my calendar for the next 7 days with calendar_list_events, then
    summarize the busiest days and suggest two focus blocks. {input}

First line: ``# name — description``. Body: the prompt; ``{input}`` receives
whatever follows the command ("/weekly around my trip" → input="around my
trip"). A user file with a builtin's name overrides it. Skills only shape
the prompt — tool permissions and confirmation cards apply unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .config import app_data_dir

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    prompt: str

    def render(self, user_input: str) -> str:
        if "{input}" in self.prompt:
            return self.prompt.replace("{input}", user_input).strip()
        if user_input:
            return f"{self.prompt.strip()}\n\nAdditional context: {user_input}"
        return self.prompt.strip()


BUILTIN_SKILLS = [
    Skill(
        name="today",
        description="Morning brief: calendar, reminders, unread email",
        prompt=(
            "Give me a short brief for today. Check, in order: today's calendar "
            "events, my open reminders, and unread email from the last day "
            "(gmail_search query 'is:unread newer_than:1d'). If a permission is "
            "missing for one of these, skip it without complaining. Finish with "
            "the two or three things that most need my attention. {input}"
        ),
    ),
    Skill(
        name="inbox",
        description="Summarize unread email",
        prompt=(
            "Summarize my unread email (gmail_search 'is:unread', then read "
            "anything that looks important). Group by sender, lead with anything "
            "urgent or time-sensitive, and keep it brief. {input}"
        ),
    ),
    Skill(
        name="focus",
        description="Find free focus time this week",
        prompt=(
            "Find me free focus slots: use time_now for today's date, then "
            "calendar_find_free_slots for the next 5 days with 90-minute "
            "duration during working hours. Suggest the two best slots. {input}"
        ),
    ),
    Skill(
        name="tidy",
        description="Propose a cleanup for an approved folder",
        prompt=(
            "Look at the files in my approved folder ({input}) with files_list. "
            "Propose a tidy-up: what to group, rename, or move to the Trash. "
            "Propose actions one at a time so I can approve each."
        ),
    ),
]


class SkillLibrary:
    def __init__(self, skills_dir: Path | None = None):
        self._dir = skills_dir if skills_dir is not None else app_data_dir() / "skills"
        self._skills: dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        skills = {s.name: s for s in BUILTIN_SKILLS}
        if self._dir.is_dir():
            for path in sorted(self._dir.glob("*.md")):
                skill = _parse_skill_file(path)
                if skill:
                    skills[skill.name] = skill  # user file wins over builtin
        self._skills = skills

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def expand(self, text: str) -> str | None:
        """Expand '/name rest of message' into the skill prompt, or None."""
        if not text.startswith("/"):
            return None
        command, _, rest = text[1:].partition(" ")
        skill = self._skills.get(command.strip().lower())
        if skill is None:
            return None
        return skill.render(rest.strip())

    def help_text(self) -> str:
        lines = ["Available commands:"]
        for name in self.names():
            lines.append(f"  /{name} — {self._skills[name].description}")
        lines.append(f"\nAdd your own: drop a .md file in {self._dir}")
        return "\n".join(lines)


def _parse_skill_file(path: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("Cannot read skill file %s: %s", path, exc)
        return None
    if not text.startswith("#"):
        log.warning("Skill file %s must start with '# name — description'", path)
        return None
    header, _, body = text.partition("\n")
    header = header.lstrip("#").strip()
    for separator in ("—", "--", ":"):
        if separator in header:
            name, _, description = header.partition(separator)
            break
    else:
        name, description = header, ""
    name = name.strip().lower().replace(" ", "-")
    body = body.strip()
    if not name or not body:
        log.warning("Skill file %s is missing a name or prompt body", path)
        return None
    return Skill(name=name, description=description.strip() or name, prompt=body)
