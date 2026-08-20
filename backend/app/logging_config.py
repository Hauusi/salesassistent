"""Logging setup, shared by all three entrypoints.

The worker and the scheduler each called `logging.basicConfig` on their
own; the API process configured nothing at all. Under uvicorn that leaves
the root logger at WARNING, so everything this codebase logs at INFO was
invisible in the API process:

  - the startup checks (app/services/startup_checks.py), including the
    `logger.exception` that development mode relies on to surface a failed
    check instead of refusing to boot - the one case where the message is
    the entire point;
  - the per-call prompt/token breakdown (app/services/token_metrics.py),
    which exists to make Claude spend auditable;
  - the coercion warnings that record a model returning something outside
    its own tool schema.

One `configure_logging()` for all three, so a process cannot come up
without it.

`token_metrics` and the worker attach structured payloads via `extra=`.
The JSON formatter below renders those; the text formatter ignores them.
Pick with LOG_FORMAT (default: text, which is what a human tailing a
terminal wants; json for anything shipping to a log aggregator).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import logging.config
from typing import Any

from app.config import get_settings

# Attributes present on every LogRecord. Anything else was passed by the
# caller via extra= and is what makes a structured log worth having.
_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"asctime", "message", "taskName"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, including anything passed via extra=."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(
                record.created, tz=dt.UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS:
                payload[key] = value

        # default=str so a UUID or datetime in an extra= payload degrades to
        # its string form instead of taking down the log call.
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    settings = get_settings()
    use_json = settings.log_format.lower() == "json"

    logging.config.dictConfig(
        {
            "version": 1,
            # uvicorn installs its own handlers before this runs; without
            # this its access log would be silenced entirely.
            "disable_existing_loggers": False,
            "formatters": {
                "text": {
                    "format": "%(asctime)s %(levelname)-8s %(name)s %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json" if use_json else "text",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"handlers": ["console"], "level": settings.log_level.upper()},
            "loggers": {
                # Route uvicorn through the same handler rather than its
                # own, so one process emits one format.
                "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
                # Very chatty at INFO and it duplicates what the app logs.
                "sqlalchemy.engine": {"level": "WARNING"},
            },
        }
    )
