"""Deterministic scoring engine (Milestone 9 / Req 9, 10, 31).

Produces the five scores — Business, Financial, Obligor, StructureProtection,
Facility — deterministically from validated ParameterResults and the versioned
``scoring`` config. Core rules:

* No LLM input: scores are pure functions of (parameters, config).
* Parameter -> risk band via configured ``parameter_bands`` thresholds.
* Topic score = weighted average of present parameter bands. Missing weighted
  dimensions are NOT silently renormalized to 100% (Remediation 3): ``coverage``
  config decides whether a partial score is ``final`` / ``provisional`` /
  ``unavailable``, and a missing CRITICAL dimension forces ``unavailable``.
* Floors/overlays (``obligor.floors``) dominate weighted averages so a severe
  risk is never averaged away.
* Evidence quality never moves a band (Req 10): it is read only to decide
  whether a result is excluded from scoring (unavailable/provisional parameter).
* Same inputs + config version => identical scores (reproducible).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from app.schemas.agentic import (
    ParameterResult,
    ParameterStatus,
    RiskScore,
    ScoreKind,
    ScoreStatus,
)
from app.services.scoring.overlays import apply_floors


@dataclass(frozen=True)
class ScoringConfig:
    """Loaded scoring configuration with its version + hash (Req 9.3)."""

    content: dict[str, Any]
    version: int
    content_hash: str

    def __getitem__(self, key: str) -> Any:
        return self.content[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.content.get(key, default)

    def rubric_engine(self):
        """Build the deterministic rubric engine from this config (items 16-17)."""
        from app.services.scoring.rubric import RubricEngine

        return RubricEngine(self.content.get("rubrics", {}),
                            config_version=self.version, config_hash=self.content_hash)

    @classmethod
    def from_registry(cls, registry, *, version: int | None = None) -> "ScoringConfig":
        """Load the ``scoring`` artifact from a ConfigRegistry (pinned or latest)."""
        if version is None:
            latest = registry.latest("scoring")
            if latest is None:
                raise ValueError(
                    "No 'scoring' configuration registered; bootstrap it first "
                    "(no silent default, Req 21)."
                )
            version = latest.version
        row = registry.get("scoring", version)
        if row is None:
            raise ValueError(f"No 'scoring' configuration at version {version}.")
        return cls(content=row.content, version=row.version,
                   content_hash=row.content_hash)


class ScoringEngine:
    """Compute the five deterministic risk scores from ParameterResults."""

    def __init__(self, config: ScoringConfig) -> None:
        self._cfg = config

    # -- parameter banding ----------------------------------------------------

    def band_parameter(self, parameter_id: str, value: float) -> int | None:
        """Map a numeric parameter value to a 1-4 band via configured thresholds."""
        bands = self._cfg.get("parameter_bands", {}).get(parameter_id)
        if not bands:
            return None
        for rule in bands:
            if "max" in rule and value <= rule["max"]:
                return int(rule["band"])
            if "min" in rule and value >= rule["min"]:
                return int(rule["band"])
            if "max" not in rule and "min" not in rule:
                return int(rule["band"])  # catch-all terminal band
        return int(bands[-1]["band"])

    # -- topic scores ---------------------------------------------------------

    def score_topic(
        self,
        kind: ScoreKind,
        parameters: Iterable[ParameterResult],
        *,
        analysis_run_id: str,
        weights_key: str,
        coverage_key: str,
    ) -> RiskScore:
        weights: dict[str, float] = self._cfg.get(weights_key, {})
        coverage_cfg = self._cfg.get("coverage", {}).get(coverage_key, {})
        total_weight = float(sum(weights.values())) or 1.0

        by_id = {p.parameter_id: p for p in parameters}
        contributing: list[str] = []
        missing: list[str] = []
        critical_missing: list[str] = []
        weighted_sum = 0.0
        present_weight = 0.0
        detail_components: dict[str, Any] = {}

        critical = set(coverage_cfg.get("critical_dimensions", []))
        for pid, weight in weights.items():
            p = by_id.get(pid)
            usable = (
                p is not None
                and p.status in (ParameterStatus.OK, ParameterStatus.PROVISIONAL)
                and isinstance(p.value, (int, float))
            )
            if not usable:
                missing.append(pid)
                if pid in critical:
                    critical_missing.append(pid)
                continue
            band = self.band_parameter(pid, float(p.value))
            if band is None:
                missing.append(pid)
                if pid in critical:
                    critical_missing.append(pid)
                continue
            contributing.append(pid)
            weighted_sum += band * weight
            present_weight += weight
            detail_components[pid] = {"value": p.value, "band": band, "weight": weight}

        coverage = present_weight / total_weight if total_weight else 0.0
        min_coverage = float(coverage_cfg.get("min_coverage", 0.0))
        allow_partial = bool(coverage_cfg.get("allow_partial", True))

        # Decide status WITHOUT renormalizing missing weights.
        if critical_missing or present_weight == 0:
            status, band = ScoreStatus.UNAVAILABLE, None
        elif coverage + 1e-9 >= 1.0:
            status = ScoreStatus.FINAL
            band = _round_band(weighted_sum / present_weight)
        elif coverage + 1e-9 >= min_coverage and allow_partial:
            status = ScoreStatus.PROVISIONAL
            band = _round_band(weighted_sum / present_weight)
        else:
            status, band = ScoreStatus.UNAVAILABLE, None

        return RiskScore(
            score_id=f"score_{uuid.uuid4().hex[:16]}",
            analysis_run_id=analysis_run_id,
            kind=kind,
            status=status,
            band=band,
            scoring_config_version=self._cfg.version,
            scoring_config_hash=self._cfg.content_hash,
            contributing_parameter_ids=contributing,
            missing_required_parameter_ids=missing,
            critical_missing_parameter_ids=critical_missing,
            coverage_weight=round(coverage, 6),
            detail={"components": detail_components, "coverage": round(coverage, 6)},
        )

    def score_business(self, parameters, *, analysis_run_id) -> RiskScore:
        return self.score_topic(
            ScoreKind.BUSINESS, parameters,
            analysis_run_id=analysis_run_id,
            weights_key="business_weights", coverage_key="business",
        )

    def score_financial(self, parameters, *, analysis_run_id) -> RiskScore:
        return self.score_topic(
            ScoreKind.FINANCIAL, parameters,
            analysis_run_id=analysis_run_id,
            weights_key="financial_weights", coverage_key="financial",
        )

    def score_structure_protection(self, parameters, *, analysis_run_id) -> RiskScore:
        """StructureProtectionScore from collateral/guarantee/covenant protection.

        The protection a transaction provides (collateral coverage, guarantee
        support, covenant package). A strong protection parameter bands LOW
        (band 1 = strong protection). When no protection evidence exists the
        score is ``unavailable`` (not fabricated), which flows through to an
        unavailable FacilityRiskScore (Req 9.12).
        """
        return self.score_topic(
            ScoreKind.STRUCTURE_PROTECTION, parameters,
            analysis_run_id=analysis_run_id,
            weights_key="structure_protection_weights",
            coverage_key="structure_protection",
        )

    # -- obligor --------------------------------------------------------------

    def score_obligor(
        self,
        business: RiskScore,
        financial: RiskScore,
        *,
        analysis_run_id: str,
        conditions: dict[str, bool] | None = None,
    ) -> RiskScore:
        """Obligor = weighted(Business, Financial) + configured floors (Req 9.5/9.7)."""
        conditions = conditions or {}
        cfg = self._cfg.get("obligor", {})
        floors = cfg.get("floors", [])

        # Apply a financial floor to the FINANCIAL band first (Req 9.7).
        fin_band = financial.band
        applied: list[str] = []
        if fin_band is not None:
            fin_overlay = apply_floors(
                fin_band, floors=floors, conditions=conditions,
                key="financial_min_band",
            )
            fin_band = fin_overlay.band
            applied.extend(f"financial:{a}" for a in fin_overlay.applied)

        if business.band is None or fin_band is None:
            # Cannot derive a defensible obligor band if a parent is unavailable.
            return RiskScore(
                score_id=f"score_{uuid.uuid4().hex[:16]}",
                analysis_run_id=analysis_run_id,
                kind=ScoreKind.OBLIGOR,
                status=ScoreStatus.UNAVAILABLE,
                scoring_config_version=self._cfg.version,
                scoring_config_hash=self._cfg.content_hash,
                contributing_parameter_ids=[business.score_id, financial.score_id],
                detail={"reason": "business or financial score unavailable"},
            )

        bw = float(cfg.get("business_weight", 0.4))
        fw = float(cfg.get("financial_weight", 0.6))
        raw = (business.band * bw + fin_band * fw) / (bw + fw)
        band = _round_band(raw)

        overlay = apply_floors(
            band, floors=floors, conditions=conditions, key="obligor_min_band",
        )
        band = overlay.band
        applied.extend(overlay.applied)

        status = (
            ScoreStatus.PROVISIONAL
            if ScoreStatus.PROVISIONAL in (business.status, financial.status)
            else ScoreStatus.FINAL
        )
        return RiskScore(
            score_id=f"score_{uuid.uuid4().hex[:16]}",
            analysis_run_id=analysis_run_id,
            kind=ScoreKind.OBLIGOR,
            status=status,
            band=band,
            scoring_config_version=self._cfg.version,
            scoring_config_hash=self._cfg.content_hash,
            contributing_parameter_ids=[business.score_id, financial.score_id],
            applied_overlays=applied,
            detail={"business_band": business.band, "financial_band": fin_band,
                    "pre_overlay_band": _round_band(raw)},
        )

    # -- facility -------------------------------------------------------------

    def score_facility(
        self,
        obligor: RiskScore,
        protection: RiskScore | None,
        *,
        analysis_run_id: str,
    ) -> RiskScore:
        """Facility = weighted(Obligor, StructureProtection) (Req 9.6).

        Keeps borrower and facility risk separate. When required structuring
        inputs are unavailable (``protection`` unavailable/None), the facility
        score is ``unavailable`` rather than fabricated (Req 9.12).
        """
        cfg = self._cfg.get("facility", {})
        if obligor.band is None:
            return self._unavailable_facility(obligor, analysis_run_id,
                                              "obligor unavailable")
        if protection is None or protection.band is None:
            return self._unavailable_facility(obligor, analysis_run_id,
                                              "structure protection unavailable")
        ow = float(cfg.get("obligor_weight", 0.7))
        pw = float(cfg.get("protection_weight", 0.3))
        # Protection REDUCES facility risk: a strong protection band (low number)
        # pulls the facility band toward lower risk, but never below the config.
        raw = (obligor.band * ow + protection.band * pw) / (ow + pw)
        band = _round_band(raw)
        status = (
            ScoreStatus.PROVISIONAL
            if ScoreStatus.PROVISIONAL in (obligor.status, protection.status)
            else ScoreStatus.FINAL
        )
        return RiskScore(
            score_id=f"score_{uuid.uuid4().hex[:16]}",
            analysis_run_id=analysis_run_id,
            kind=ScoreKind.FACILITY,
            status=status,
            band=band,
            scoring_config_version=self._cfg.version,
            scoring_config_hash=self._cfg.content_hash,
            contributing_parameter_ids=[obligor.score_id, protection.score_id],
            detail={"obligor_band": obligor.band, "protection_band": protection.band},
        )

    def _unavailable_facility(
        self, obligor: RiskScore, analysis_run_id: str, reason: str
    ) -> RiskScore:
        return RiskScore(
            score_id=f"score_{uuid.uuid4().hex[:16]}",
            analysis_run_id=analysis_run_id,
            kind=ScoreKind.FACILITY,
            status=ScoreStatus.UNAVAILABLE,
            scoring_config_version=self._cfg.version,
            scoring_config_hash=self._cfg.content_hash,
            contributing_parameter_ids=[obligor.score_id],
            detail={"reason": reason},
        )


def _round_band(value: float) -> int:
    """Round a weighted band to the nearest 1-4 integer band."""
    return max(1, min(4, int(math.floor(value + 0.5))))
