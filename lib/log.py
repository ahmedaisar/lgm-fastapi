"""Port of lib/log.js — leveled logging with ISO timestamps.

Line format mirrors the Express service: `2026-11-01T06:43:12.345Z INFO message`.
"""

import logging
import sys
import time
from datetime import datetime, timezone

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


class _UtcFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z"


def _level_name() -> str:
    # Read lazily so tests / hot reloads can set LOG_LEVEL before first log call.
    from . import config

    return config.LOG_LEVEL if config.LOG_LEVEL in _LEVELS else "info"


_logger = logging.getLogger("lgm")
_logger.setLevel(logging.DEBUG)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(_UtcFormatter("%(message)s"))
_logger.addHandler(_handler)
_logger.propagate = False


def _log_at(name: str, msg: str, *args, exc_info=False) -> None:
    if _LEVELS.get(name, 20) < _LEVELS[_level_name()]:
        return
    message = msg % args if args else msg
    _logger.log(_LEVELS[name], "%s %s", name.upper(), message, exc_info=exc_info)


def debug(msg: str, *args) -> None:
    _log_at("debug", msg, *args)


def info(msg: str, *args) -> None:
    _log_at("info", msg, *args)


def warn(msg: str, *args) -> None:
    _log_at("warn", msg, *args)


def error(msg: str, *args, exc_info=False) -> None:
    _log_at("error", msg, *args, exc_info=exc_info)
