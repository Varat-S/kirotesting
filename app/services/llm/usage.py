"""Token meters and per-run cost estimates.

Every live provider backend reports the provider-supplied token usage of each
billed call twice: to the process-wide meter (what a CLI run reports at its
end) and to the backend instance's own ``usage`` meter. The web app runs
several jobs at once in one process, each on backends built for it, so a job's
cost is the sum of its own backends' meters. Metering sits at the backend
rather than in :class:`LLMClient` because some paths call the backend
directly. Scripted, fake and replayed backends never report, so an offline run
costs nothing.

Prices are configuration (``LLM_PRICES``, USD per million tokens, keyed by
model id), never hard-coded: list prices change and differ by region. A model
without a price is reported by its token counts with the cost left open, never
guessed. The figure is an estimate from list prices, not a provider invoice.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable

_FIELDS = ("input_tokens", "output_tokens", "thinking_tokens")


class Meter:
    """Token totals per model; thread-safe (BO and FO call the provider in parallel)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_model: dict[str, dict[str, int]] = {}

    def record(self, model_id: str, usage: dict[str, Any]) -> None:
        with self._lock:
            totals = self._by_model.setdefault(model_id, {"calls": 0, **dict.fromkeys(_FIELDS, 0)})
            totals["calls"] += 1
            for key in _FIELDS:
                totals[key] += int(usage.get(key) or 0)

    def reset(self) -> None:
        with self._lock:
            self._by_model.clear()

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {m: dict(t) for m, t in self._by_model.items()}


_process = Meter()


def record(model_id: str, usage: dict[str, Any], meter: Meter | None = None) -> None:
    """Add one billed call to the process meter and, when given, the backend's own meter."""
    _process.record(model_id, usage)
    if meter is not None:
        meter.record(model_id, usage)


def reset() -> None:
    _process.reset()


def meter_of(backend) -> Meter | None:
    """A backend's own meter; None for scripted/fake backends, which are never billed."""
    meter = getattr(backend, "usage", None)
    return meter if isinstance(meter, Meter) else None


def summary(prices: dict[str, dict[str, float]] | None = None,
            meters: Iterable[Meter | None] | None = None) -> dict[str, Any]:
    """Tokens and estimated USD cost per model and in total.

    Covers ``meters`` combined when given (None entries are skipped), else the
    process meter since :func:`reset`.
    """
    if prices is None:
        from app.core.config import get_settings

        prices = get_settings().llm_prices
    snapshot: dict[str, dict[str, int]] = {}
    for meter in ([_process] if meters is None else meters):
        for model, totals in (meter.snapshot() if meter is not None else {}).items():
            into = snapshot.setdefault(model, dict.fromkeys(totals, 0))
            for key, value in totals.items():
                into[key] += value
    models, total, unpriced = {}, 0.0, []
    for model, totals in sorted(snapshot.items()):
        price = prices.get(model)
        entry = dict(totals)
        if price is None:
            entry["cost_usd"] = None
            unpriced.append(model)
        else:
            # Thinking tokens are billed at the output rate.
            entry["cost_usd"] = round(
                (totals["input_tokens"] * price["input"]
                 + (totals["output_tokens"] + totals["thinking_tokens"]) * price["output"]) / 1_000_000, 4)
            total += entry["cost_usd"]
        models[model] = entry
    cost = {
        "calls": sum(t["calls"] for t in snapshot.values()),
        "tokens": sum(t[k] for t in snapshot.values() for k in _FIELDS),
        "total_cost_usd": round(total, 4) if not unpriced else None,
        "priced_cost_usd": round(total, 4),
        "unpriced_models": unpriced,
        "by_model": models,
        "note": "Estimated from LLM_PRICES (USD per million tokens); not a provider invoice.",
    }
    cost["text"] = _text(cost)
    return cost


def _text(cost: dict[str, Any]) -> str:
    if not cost["calls"]:
        return "no model calls"
    usage = f"{cost['calls']} model call(s), {cost['tokens']:,} tokens"
    if cost["total_cost_usd"] is not None:
        return f"~US${cost['total_cost_usd']:.4f} ({usage})"
    priced = f"~US${cost['priced_cost_usd']:.4f} priced, " if cost["priced_cost_usd"] else ""
    return f"{usage}; {priced}no price set for {', '.join(cost['unpriced_models'])} (add it to LLM_PRICES in .env)"


def describe(cost: dict[str, Any]) -> str:
    """One line for the end of a CLI run."""
    return "Cost: " + cost["text"]
