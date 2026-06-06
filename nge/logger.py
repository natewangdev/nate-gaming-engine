"""Lightweight logging helper for the nge package."""

from __future__ import annotations

import logging
import os

_DEFAULT_LEVEL = os.environ.get("NGE_LOG_LEVEL", "INFO").upper()
_configured = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger("nge")
    root.addHandler(handler)
    root.setLevel(_DEFAULT_LEVEL)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under the ``nge`` root."""
    _configure_root()
    short = name.split(".")[-1]
    return logging.getLogger(f"nge.{short}")


_LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def print(
    *args: object,
    sep: str = " ",
    end: str = "\n",
    flush: bool = False,
    level: str = "info",
) -> None:
    """Print via the nge logger (drop-in for built-in ``print``).

    Pass ``level`` to control severity: ``debug``, ``info``, ``warning``,
    ``error``, or ``critical``.
    """
    msg = sep.join(str(a) for a in args)
    if end and end != "\n":
        msg += end
    log_level = _LEVELS.get(level.lower(), logging.INFO)
    logger.log(log_level, msg.rstrip("\n"))
    if flush:
        for handler in logging.getLogger("nge").handlers:
            handler.flush()


# User-facing logger: ``nge.logger.info(...)``, ``nge.logger.error(...)``, etc.
logger = get_logger("logger")
