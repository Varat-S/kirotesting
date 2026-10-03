"""Blinded prompt/model A/B testing (task 9.5, Req 23.11, 23.14).

Runs the SAME case through two prompt/model variants and compares them on
OBJECTIVE metrics against a declared manifest -- NEVER on "which sounds better"
(Req 23.11). Compared metrics:

* material-risk precision / recall
* omissions (expected material risks the variant failed to surface)
* unsupported claims
* groundedness (share of claims that are grounded)
* analyst corrections
* review time

**Blinding (headline of 9.5).** The scoring step receives the two variants'
outputs under OPAQUE labels (``A`` / ``B``) assigned by the runner, and the
mapping back to the real variant identity (e.g. prompt/model id) is withheld
until AFTER scoring. :func:`run_blinded_ab` returns the per-label scores plus a
sealed ``unblinding`` map; the scorer function never sees the real identities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from app.services.evaluation.manifest import GroundTruthManifest
from app.services.evaluation.stats import precision_recall_f1


@dataclass
class VariantOutput:
    """One variant's output for a case (produced via the deterministic backend).

    ``variant_id`` is the TRUE identity (prompt/model). It is withheld from the
    scorer by the runner's blinding step.
    """

    variant_id: str
    surfaced_risks: list[str]  # risk ids the variant surfaced
    claims: list[Mapping[str, Any]]  # each: {grounded: bool, supported: bool}
    analyst_corrections: int = 0
    review_time_seconds: float = 0.0


@dataclass
class BlindedScore:
    """Objective scores for one blinded label (``A`` or ``B``)."""

    label: str
    risk_precision: float
    risk_recall: float
    omissions: int
    unsupported_claims: int
    groundedness: float
    analyst_corrections: int
    review_time_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "risk_precision": self.risk_precision,
            "risk_recall": self.risk_recall,
            "omissions": self.omissions,
            "unsupported_claims": self.unsupported_claims,
            "groundedness": self.groundedness,
            "analyst_corrections": self.analyst_corrections,
            "review_time_seconds": self.review_time_seconds,
        }


@dataclass
class ABResult:
    """A blinded A/B comparison result."""

    case_id: str
    manifest_version: str
    scores: dict[str, BlindedScore] = field(default_factory=dict)
    # Sealed until after scoring: label -> true variant id.
    unblinding: dict[str, str] = field(default_factory=dict)
    blinded: bool = True

    def winner_by(self, metric: str) -> str | None:
        """Return the LABEL with the higher value for ``metric`` (ties -> None)."""
        ranked = sorted(
            self.scores.values(),
            key=lambda s: getattr(s, metric),
            reverse=True,
        )
        if len(ranked) < 2 or getattr(ranked[0], metric) == getattr(ranked[1], metric):
            return None
        return ranked[0].label

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "manifest_version": self.manifest_version,
            "blinded": self.blinded,
            "scores": {k: v.as_dict() for k, v in self.scores.items()},
            "unblinding": dict(self.unblinding),
        }


def _score_blinded(
    label: str,
    surfaced_risks: list[str],
    claims: list[Mapping[str, Any]],
    analyst_corrections: int,
    review_time_seconds: float,
    manifest: GroundTruthManifest,
) -> BlindedScore:
    """Score ONE blinded output. Receives no variant identity (blinding)."""
    expected = set(manifest.adjudicated_material_risks)
    surfaced = set(surfaced_risks)

    y_true: list[int] = []
    y_pred: list[int] = []
    universe = sorted(expected | surfaced)
    for risk in universe:
        y_true.append(1 if risk in expected else 0)
        y_pred.append(1 if risk in surfaced else 0)
    prf = precision_recall_f1(y_true, y_pred)

    omissions = len(expected - surfaced)
    unsupported = sum(1 for c in claims if not c.get("supported", False))
    grounded = sum(1 for c in claims if c.get("grounded", False))
    groundedness = grounded / len(claims) if claims else 1.0

    return BlindedScore(
        label=label,
        risk_precision=prf.precision,
        risk_recall=prf.recall,
        omissions=omissions,
        unsupported_claims=unsupported,
        groundedness=groundedness,
        analyst_corrections=analyst_corrections,
        review_time_seconds=review_time_seconds,
    )


def run_blinded_ab(
    variant_a: VariantOutput,
    variant_b: VariantOutput,
    *,
    manifest: GroundTruthManifest | None,
) -> ABResult:
    """Run a blinded A/B comparison of two variant outputs against the manifest.

    The runner assigns opaque labels ``A``/``B`` and scores each output WITHOUT
    revealing its true ``variant_id`` to the scorer; the ``unblinding`` map is
    attached only after scoring. Raises when no manifest is declared (Req 23.5).
    """
    if manifest is None:
        raise ValueError(
            "run_blinded_ab requires a declared GroundTruthManifest (Req 23.5)."
        )

    # Assign opaque labels. The scorer below receives ONLY the label + outputs,
    # never the variant_id -- this is the blinding boundary.
    labelled = {"A": variant_a, "B": variant_b}

    result = ABResult(
        case_id=manifest.case_id, manifest_version=manifest.manifest_version
    )
    for label, output in labelled.items():
        result.scores[label] = _score_blinded(
            label,
            output.surfaced_risks,
            output.claims,
            output.analyst_corrections,
            output.review_time_seconds,
            manifest,
        )
    # Unblind only AFTER scoring completes.
    result.unblinding = {label: labelled[label].variant_id for label in labelled}
    return result


def score_with_variant_identity(*args: Any, **kwargs: Any) -> None:  # noqa: D401
    """Intentionally absent: scoring must never receive variant identity.

    Blinding (Req 23.11) is structural: there is no scoring entry point that
    accepts a variant's true identity. This stub exists to document the
    guarantee and will raise if called.
    """
    raise NotImplementedError(
        "A/B scoring is blinded: the scorer never receives variant identity "
        "until after scoring (Req 23.11)."
    )
