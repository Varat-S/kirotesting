"""Fiscal-year input pools shared by the metric stage and sector benchmarking.

Extracted verbatim from ``CreditMemoPipeline._metrics`` so both consumers apply
the SAME eligibility rule: only annual (``FY``) duration and ``instant``
observations that are consolidated, non-dimensional, GAAP and dated on or before
the as-of date are eligible. Quarterly / YTD observations stay in the evidence
snapshot and never reach a ratio.
"""

from __future__ import annotations

import json
from collections import defaultdict

from app.schemas.enums import FactStatus
from app.services.metrics.engine import MetricInput


def fiscal_pools(evidence):
    """Return ``(pools, facts_by_id)``; pools are keyed ``(entity, year, end)``."""
    pools = defaultdict(lambda: defaultdict(list))
    facts_by_id = {f["fact_id"]: f for f in evidence.facts}
    for key, dq in evidence.data_quality.items():
        entity, year, period_type, end, name, *identity = json.loads(key)
        dimensional = identity and bool(identity[0])
        selected = facts_by_id.get(dq.selected_fact_id, {})
        basis = (
            identity[2] if len(identity) > 2 else selected.get("accounting_basis")
        )
        consolidated = selected.get("consolidation_scope") in {None, "consolidated"}
        if (
            year is not None
            and period_type in {"FY", "instant"}
            and not dimensional
            and consolidated
            and basis in {None, "GAAP"}
            and end <= evidence.as_of_date.isoformat()
        ):
            pools[(entity, int(year), end)][name].append(dq)
    return pools, facts_by_id


def metric_inputs(fields, facts_by_id, evidence):
    """Resolve one period's fields into ``MetricInput`` objects.

    Two competing states for a field make it ``conflicting``; a deterministic
    derived fact that no human has verified is ``unverified``.
    """
    inputs = {}
    for name, states in fields.items():
        if len(states) != 1:
            inputs[name] = MetricInput(name, None, FactStatus.CONFLICTING)
        else:
            dq = states[0]
            inputs[name] = MetricInput(
                name,
                dq.value,
                FactStatus.UNVERIFIED
                if facts_by_id.get(dq.selected_fact_id, {}).get("created_by")
                == "deterministic_derived"
                and dq.selected_fact_id
                not in evidence.provenance.get("human_verified_fact_ids", [])
                else FactStatus(dq.state),
                dq.selected_fact_id,
            )
    return inputs
