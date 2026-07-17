"""Structured local logging with secret/content redaction.

Policy: log *events* (tool invoked, action approved, request failed), never
message bodies, email contents, or tokens. The redacting filter is a second
line of defense for anything that slips through.
"""

from __future__ import annotations

import logging
import logging.handlers
import re

from .config import app_log_dir

_REDACTIONS = [
    # OAuth / API tokens and bearer headers
    (re.compile(r"(?i)(bearer\s+)[a-z0-9\-._~+/=]{8,}"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)(token|secret|password|authorization)[\"']?\s*[:=]\s*[\"']?[^\s\"',}]{4,}"
        ),
        r"\1=[REDACTED]",
    ),
    # Google OAuth refresh/access token shapes
    (re.compile(r"ya29\.[A-Za-z0-9\-._]+"), "[REDACTED]"),
    (re.compile(r"1//[A-Za-z0-9\-._]+"), "[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def setup_logging(level: int = logging.INFO) -> None:
    log_dir = app_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_dir / "hearth.log", maxBytes=2_000_000, backupCount=3
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    console.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    root.addHandler(console)
