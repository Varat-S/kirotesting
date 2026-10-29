"""Stopping a Financial Overview run from the browser, and showing which step it is on.

The run form carries a random token. Its Stop button posts that token to ``/fo/run/cancel``, and the running request
checks ``stop_requested(token)`` between steps (before each pre-check's model call, before scoring, before
recording). The run then ends with ``FoRunCancelled`` and records nothing. A model call already in flight can't be
interrupted: the stop takes effect when it returns.

The same calls report progress: each ``check_stop(step)`` records the step the run is starting, so the page can
poll ``/fo/run/progress`` while the run is under way, and an error names the step it happened in.

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
_progress: dict[str, dict] = {}  # token -> {"started": t, "steps": [{"step": str, "at": t}], "work": {...}}
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
        _progress.pop(token or "", None)


def set_step(token: str | None, step: str) -> None:
    """Record that the run with this token is starting ``step``."""
    if not valid(token):
        return
    now = time.time()
    with _lock:
        for t, p in list(_progress.items()):
            if now - p["started"] > KEEP_SECONDS:
                del _progress[t]
        _progress.setdefault(token, {"started": now, "steps": []})["steps"].append({"step": step, "at": now})


def set_work(token: str | None, done: int, total: int) -> None:
    """Record how many of the run's steps are done, of how many (the runner's ``on_work``)."""
    with _lock:
        p = _progress.get(token or "")
        if p is not None:
            p["work"] = {"done": done, "total": total}


def progress(token: str | None) -> dict:
    """Where the run with this token is: the step under way (``None`` before the first), its number, the steps
    done, ``work`` (steps done and in the run, for the progress bar; ``None`` before the first) and the seconds
    since the run started."""
    with _lock:
        p = _progress.get(token or "")
        if not p:
            return {"step": None, "number": 0, "done": [], "work": None, "elapsed": 0}
        steps = [s["step"] for s in p["steps"]]
        return {"step": steps[-1], "number": len(steps), "done": steps[:-1], "work": p.get("work"),
                "elapsed": round(time.time() - p["started"])}


def checker(token: str | None):
    """A callable the runner calls between steps: records the step it is starting, and raises ``FoRunCancelled``
    once a stop is requested."""
    def check(step: str) -> None:
        set_step(token, step)
        if stop_requested(token):
            raise FoRunCancelled(f"Run stopped by the analyst before {step}; nothing was recorded.")
    return check
