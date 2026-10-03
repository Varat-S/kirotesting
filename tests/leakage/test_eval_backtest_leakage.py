"""Temporal backtest + leakage tests for the eval harness (task 9.3).

Extends the Milestone 2 leakage tests to the Milestone 9 backtest harness
(Req 19.7, 20.4, 23.7):

* an expanding-window backtest admits only items available by each fold cutoff
  and REJECTS future-dated filing/news/peer/market-macro items -- never mixing
  future into a past fold;
* a rolling-window design is reported not-applicable (no learned model);
* the two visibly distinguished re-run modes resolve to different, labelled
  config versions.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.evaluation import (
    BacktestItem,
    RerunController,
    RerunMode,
    reject_future_item,
    run_expanding_window_backtest,
)

T0 = datetime(2023, 6, 30, tzinfo=timezone.utc)
T1 = datetime(2023, 12, 31, tzinfo=timezone.utc)
CUTOFF = datetime(2024, 1, 15, tzinfo=timezone.utc)
FUTURE = datetime(2024, 6, 30, tzinfo=timezone.utc)


def _items() -> list[BacktestItem]:
    return [
        BacktestItem("filing-h1", T0, "filing"),
        BacktestItem("news-h2", T1, "news"),
        BacktestItem("peer-future", FUTURE, "peer"),
        BacktestItem("macro-future", FUTURE, "market_macro"),
    ]


def test_expanding_window_rejects_future_items_each_fold() -> None:
    result = run_expanding_window_backtest(_items(), [T0, T1, CUTOFF])
    assert result.design == "expanding_window"

    # Fold at T0: only the T0 filing is admitted; everything later is rejected.
    fold0 = result.folds[0]
    assert fold0.admitted == ["filing-h1"]
    assert fold0.rejected["news-h2"] == "future_dated"
    assert fold0.rejected["peer-future"] == "future_dated"

    # Final fold at the cutoff: both historical items admitted, both future
    # items rejected -- future NEVER mixed into the past (Req 23.7).
    final = result.folds[-1]
    assert set(final.admitted) == {"filing-h1", "news-h2"}
    assert set(final.rejected) == {"peer-future", "macro-future"}
    assert all(r == "future_dated" for r in final.rejected.values())


def test_rolling_window_not_applicable_without_learned_model() -> None:
    result = run_expanding_window_backtest(_items(), [CUTOFF])
    assert result.rolling_window_applicable is False
    assert "no learned" in result.rolling_window_note.lower()


def test_reject_future_item_helper_across_data_types() -> None:
    for data_type in ("filing", "news", "peer", "market_macro"):
        item = BacktestItem(f"{data_type}-x", FUTURE, data_type)
        rejected, reason = reject_future_item(item, CUTOFF)
        assert rejected is True
        assert reason == "future_dated"

    ok_item = BacktestItem("ok", T0, "filing")
    rejected, reason = reject_future_item(ok_item, CUTOFF)
    assert rejected is False
    assert reason == "eligible"


def test_two_rerun_modes_are_visibly_distinguished() -> None:
    controller = RerunController(
        original_versions={"tolerances": 1, "escalation_rules": 1},
        latest_versions={"tolerances": 2, "escalation_rules": 3},
    )
    assert controller.distinguishable() is True

    original = controller.resolve(RerunMode.REPRODUCE_HISTORICAL)
    latest = controller.resolve(RerunMode.REEVALUATE_LATEST)

    assert original.mode is RerunMode.REPRODUCE_HISTORICAL
    assert "ORIGINAL" in original.label
    assert original.config_versions == {"tolerances": 1, "escalation_rules": 1}

    assert latest.mode is RerunMode.REEVALUATE_LATEST
    assert "LATEST" in latest.label
    assert latest.config_versions == {"tolerances": 2, "escalation_rules": 3}

    # The two modes are distinct and labelled differently.
    assert original.config_versions != latest.config_versions
    assert original.label != latest.label
