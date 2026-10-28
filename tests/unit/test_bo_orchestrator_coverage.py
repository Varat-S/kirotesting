"""The BO memo's main sections must cover every usable specialist finding."""

from app.schemas.bo import HypothesisRecord
from app.services.bo.synthesis import include_all_validated_findings


def finding(hid, section, *, direction="neutral", evidence=("source-1",),
            components=(), admissibility="established"):
    return HypothesisRecord(
        hypothesis_id=hid, analysis_run_id="run", agent_id=section,
        agent_run_id="agent-run", section=section, claim=f"Finding {hid}",
        claim_type="inference", direction=direction,
        disposition="supported", admissibility=admissibility,
        supporting_evidence_ids=list(evidence),
        component_hypothesis_ids=list(components),
    )


def test_missing_findings_enter_their_own_main_sections():
    first = finding("M1S1-H001", "M1S1")
    second = finding("M1S5-H001", "M1S5", direction="negative",
                     evidence=("source-2",))
    rejected = finding("M1S3-H001", "M1S3", admissibility="audit_only")
    parsed = {"overall_assessment": {"claim_id": "EX-01"},
              "key_drivers": [{"claim_id": "DRV-01", "section": "M1S1",
                               "hypothesis_ids": [first.hypothesis_id]}]}
    added = include_all_validated_findings(parsed, {
        h.hypothesis_id: h for h in (first, second, rejected)})
    assert [a["hypothesis_id"] for a in added] == [second.hypothesis_id]
    assert parsed["material_risks"][0]["section"] == "M1S5"
    assert parsed["material_risks"][0]["evidence_ids"] == ["source-2"]
    assert len(parsed["key_drivers"]) == 1


def test_interaction_inherits_validated_component_provenance():
    base = finding("M1S3-H001", "M1S3", evidence=("source-3",))
    interaction = finding("M1A6-H001", "M1A6", direction="negative",
                          evidence=(), components=(base.hypothesis_id,))
    parsed = {"overall_assessment": {"claim_id": "EX-01"}}
    include_all_validated_findings(parsed, {h.hypothesis_id: h for h in (base, interaction)})
    claim = next(c for c in parsed["material_risks"] if c["section"] == "M1A6")
    assert claim["hypothesis_ids"] == [interaction.hypothesis_id, base.hypothesis_id]
    assert claim["evidence_ids"] == ["source-3"]
