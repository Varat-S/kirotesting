"""Stopping a Financial Overview run from the browser.

The run form carries a random token. Its Stop button posts that token to ``/fo/run/cancel``, and the running request
checks ``stop_requested(token)`` between steps (before each pre-check's model call, before scoring, before
recording). The run then ends with ``FoRunCancelled`` and records nothing. A model call already in flight can't be
interrupted: the stop takes effect when it returns.

The registry is in memory, so it works within one server process (``python -m app.preview``); with several worker
processes a stop could reach a different process than the run.
"""

from __future__ import annotations

import re
import threading
import time

TOKEN = re.compile(r"[A-Za-z0-9_-]{8,64}")
KEEP_SECONDS = 3600

_stops: dict[str, float] = {}
_lock = threading.Lock()


class FoRunCancelled(RuntimeError):
    """The analyst stopped the run; nothing was recorded."""


def valid(token: str | None) -> bool:
    return bool(token) and TOKEN.fullmatch(token) is not None


def request_stop(token: str) -> bool:
    """Mark the run with this token to stop (also when the run hasn't reached the server yet)."""
    if not valid(token):
        return False
    now = time.time()
    with _lock:
        for t, at in list(_stops.items()):
            if now - at > KEEP_SECONDS:
                del _stops[t]
        _stops[token] = now
    return True


def stop_requested(token: str | None) -> bool:
    if not valid(token):
        return False
    with _lock:
        return token in _stops


def clear(token: str | None) -> None:
    with _lock:
        _stops.pop(token or "", None)


def checker(token: str | None):
    """A callable the runner calls between steps: raises ``FoRunCancelled`` once a stop is requested."""
    def check(step: str) -> None:
        if stop_requested(token):
            raise FoRunCancelled(f"Run stopped by the analyst before {step}; nothing was recorded.")
    return check
