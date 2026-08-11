"""Structured, secret-safe logging.

Two rules, enforced by `RedactingFilter` rather than by reviewer discipline:

1. Anything that looks like a credential is replaced before it is written -
   `password=`/`token=`/`secret=`/`api_key=` values, and the userinfo section
   of any URL (`postgresql://user:pw@host` -> `postgresql://user:***@host`).
2. Log records carry structured fields (`event`, `command_id`, `controller_id`,
   ...) so failures are diagnosable without pasting payloads into the message.

Applies to the message *and* to every structured field, so a secret cannot slip
in through `extra=`.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

LOGGER_NAME = "findit"

REDACTED = "***"

# key=value / key: value / "key": "value" for credential-ish keys
_KV_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)\b"
    r"(\"?\s*[:=]\s*\"?)([^\s,;&\"'}]+)"
)
# The userinfo part of a URL: scheme://user:password@host
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s:@]+):([^/\s@]+)@")

STRUCTURED_KEYS = (
    "event",
    "command_id",
    "controller_id",
    "drawer_number",
    "led_index",
    "status",
    "attempt",
    "reason",
)


def redact(value: Any) -> Any:
    """Redact credentials in a string, recursively in dicts and lists."""
    if isinstance(value, str):
        out = _URL_CREDENTIALS.sub(rf"\1\2:{REDACTED}@", value)
        return _KV_PATTERN.sub(rf"\1\2{REDACTED}", out)
    if isinstance(value, dict):
        return {k: (REDACTED if _is_secret_key(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)
    return value


def _is_secret_key(key: Any) -> bool:
    return isinstance(key, str) and bool(
        re.fullmatch(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization)", key)
    )


class RedactingFilter(logging.Filter):
    """Last line of defence: scrub the record before any handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = redact(record.args)
        for key in list(vars(record)):
            if key in STRUCTURED_KEYS or key.startswith("ctx_"):
                setattr(record, key, redact(getattr(record, key)))
        return True


class StructuredFormatter(logging.Formatter):
    """One JSON object per line - greppable, and safe to paste into evidence."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in STRUCTURED_KEYS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        for key, value in vars(record).items():
            if key.startswith("ctx_"):
                payload[key[4:]] = value
        if record.exc_info:
            # Type and message only - a traceback can carry connection strings.
            exc_type, exc, _ = record.exc_info
            payload["error"] = exc_type.__name__ if exc_type else "Exception"
            payload["error_message"] = redact(str(exc))[:200]
        return json.dumps(payload, default=str, sort_keys=True)


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(level: int = logging.INFO, stream=None) -> logging.Logger:
    """Idempotent: safe to call from both uvicorn startup and tests."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(StructuredFormatter())
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    return logger
