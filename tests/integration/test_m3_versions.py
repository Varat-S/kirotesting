"""The per-run choice between M3-v1 (default) and M3-v2 (the rebuild)."""

from __future__ import annotations

import shutil

import pytest

from app.services import bo_fo
from app.services.agents.registry import default_registry
from app.services.bo.m3_version import check_m3_version, is_m3_v2_block
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.runner import BusinessOverviewRunner
from tests import bo_helpers as h
from tests.integration.test_bo_fo_combined import (  # noqa: F401  (fixtures)
    _count,
    _database,
    client,
    root,
)
from tests.integration.test_bo_v3_complete import _script
from tests.integration.test_fo_runner_api import FOLDER, NVDA, agent


def test_version_defaults_to_v1_and_rejects_unknown_values():
    assert check_m3_version(None) == "v1"
    assert check_m3_version("v2") == "v2"
    with pytest.raises(ValueError, match="m3_version"):
        check_m3_version("v3")
    with pytest.raises(ValueError, match="m3_version"):
        BusinessOverviewRunner(None, m3_version="v3")


def test_only_m3_v1_schedules_the_corporate_management_specialist():
    v1 = default_registry("v3")
    v2 = default_registry("v3", m3_version="v2")
    assert "m3_corporate_management" in {a.agent_id for a in v1.all()}
    assert "m3_corporate_management" not in {a.agent_id for a in v2.all()}
    assert v1.version != v2.version


@pytest.mark.parametrize("m3_version", ["v1", "v2"])
def test_each_version_produces_its_own_m3_module_and_records_the_choice(
        db_session, tmp_path, m3_version):
    backend = ScriptedBoBackend(_script())
    result = BusinessOverviewRunner(
        db_session, backend=backend, bo_roster="v3", m3_version=m3_version,
        data_root=tmp_path / "data", output_root=tmp_path / "out",
    ).run_prepared(h.CASE, h.evidence_snapshot(), h.metrics(), write_outputs=False)
    m3 = result.handoff["module_conclusions"]["M3"]
    assert is_m3_v2_block(m3) is (m3_version == "v2")
    if m3_version == "v1":
        assert "m3_assessment_status" in m3
    else:
        assert backend.calls("m3_corporate_management") == 0
        assert {c["status"] for c in m3["m3_rebuild"].values()} == {"not_attempted"}


def test_so_assessment_from_m3_v1_keeps_its_1_1_shape():
    from app.services.structural_overview.adapter import adapt_business_overview
    from tests.integration.test_structural_overview_thin_slice import _handoff

    assessment = adapt_business_overview(_handoff())
    dumped = assessment.model_dump(mode="json")
    assert dumped["schema_version"] == "structural-overview-assessment-1.1"
    assert "m3_rebuild" not in dumped and "management_claims" not in dumped
    with pytest.raises(ValueError, match="1.2"):
        type(assessment).model_validate({**dumped, "management_claims": [
            {"observation_id": "o", "executive": None, "direction": "neutral",
             "category": "continuity", "text": "t", "citation_ids": []}]})


def test_combined_run_passes_m3_v2_to_the_bo(root, tmp_path):
    shutil.copytree(NVDA, root / FOLDER)
    db = _database(tmp_path)
    out = bo_fo.run_bo_fo(database=db, folder=FOLDER, bo_backend=None, fo_backend=agent(),
                          fo_mode="scripted", output_root=tmp_path / "out", m3_version="v2")
    assert out["bo"]["status"] == "completed", out
    assert out["bo"]["summary"]["m3_version"] == "v2"
    # No M3-v1 standalone run: only the BO's own analysis run is recorded.
    assert _count(db, "agentic_analysis_runs") == 1


def test_run_page_offers_both_versions_and_starts_m3_v2(client, root, monkeypatch):
    shutil.copytree(NVDA, root / FOLDER)
    picked = []
    monkeypatch.setattr(bo_fo, "m3_v2_live_backend", lambda model=None: picked.append(model) or object())
    started_with = {}
    monkeypatch.setattr(bo_fo.jobs, "start", lambda **kw: started_with.update(kw) or "job1")
    page = client.get("/run").text
    assert 'name="m3_version"' in page and 'value="v1" selected' in page and 'value="v2"' in page
    response = client.post("/run/start", data={"folder": FOLDER, "m3_version": "v2"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert started_with["m3_version"] == "v2" and started_with["m3_backend"] is not None
    assert picked


# --- M3-v2 from a filing folder: the SEC HTML 10-K and m3_corporate_structure.json ---------------------

def _html_packet(passages, *, form="10-K", report_date="2025-12-31"):
    from app.core.hashing import content_hash

    narrative = [{"evidence_id": content_hash(text), "document_id": "doc10k", "location": i * 1000,
                  "section": "Item 1", "text": text} for i, text in enumerate(passages)]
    return {"context": {"company": "Example Corp", "filing_period": "2025-12-31",
                        "management_document_id": "doc10k", "management_document_sha256": "sha",
                        "management_document_format": "sec_inline_xbrl", "management_document_form": form,
                        "management_document_report_date": report_date, "management_document_cik": "1"},
            "evidence_ids": [n["evidence_id"] for n in narrative], "narrative_evidence": narrative,
            "packet_hash": "hash", "snapshot_version": 1}


HTML_ROSTER = [
    "Our business is described above.\nInformation about our Executive Officers\n"
    "As of February 1, 2026, our executive officers were as follows:\nName\nAge\nPosition(s)\n"
    "Alex Sample\n58\nChief Executive Officer\nJamie Example\n51\nChief Financial Officer\n",
    "Alex Sample has served as Chief Executive Officer since 2019.\n"
    "Jamie Example has served as Chief Financial Officer since 2021.\n",
    "Item 1A. Risk Factors\nOur business faces risks.",
]


def test_html_10k_executive_roster_is_discovered_without_pages_or_cover():
    from app.services.bo.m3_rebuild.sources import sources_from_evidence_packet

    found = sources_from_evidence_packet(_html_packet(HTML_ROSTER))
    assert [(r["name"], r["position"]) for r in found.expected_roster] == [
        ("Alex Sample", "Chief Executive Officer"), ("Jamie Example", "Chief Financial Officer")]
    assert found.coverage_verifiable
    assert found.source_path.startswith("sec-inline-xbrl:")
    assert set(found.physical_pages.values()) == {1}
    # Quotations are checked against the original passage text, not the folded table.
    first = found.section_ids[0]
    assert found.check_quote(first, "Alex Sample\n58\nChief Executive Officer")
    # The section stops at the next Item heading.
    assert all("Item 1A" not in found.pages[s] for s in found.section_ids)


@pytest.mark.parametrize("form,report_date", [("10-Q", "2025-12-31"), ("10-K", "2024-12-31")])
def test_html_10k_metadata_must_match_the_case(form, report_date):
    from app.services.bo.m3_rebuild.sources import sources_from_evidence_packet

    with pytest.raises(ValueError, match="Form 10-K|period"):
        sources_from_evidence_packet(_html_packet(HTML_ROSTER, form=form, report_date=report_date))


def test_package_accepts_the_sec_10k_as_the_management_source(root):
    from app.services.acquisition import shared_filing
    from app.services.pipeline.sec import sec_source_package

    shutil.copytree(NVDA, root / FOLDER)
    bundle = shared_filing.load_bundle(FOLDER)
    package = bo_fo.m3_v2_package(
        sec_source_package(bundle, cutoff=max(f.available_at for f in bundle.files)), FOLDER, bundle)
    assert package.module3.management_source == bundle.primary_document
    assert package.module3.corporate_source is None  # no JSON in the folder


def test_combined_m3_v2_run_takes_the_corporate_structure_from_the_folder(root, tmp_path):
    import json
    from pathlib import Path

    from app.services.acquisition import shared_filing

    folder = root / FOLDER
    shutil.copytree(NVDA, folder)
    (folder / "company.json").write_text(json.dumps({"ticker": "NVDA", "name": "NVIDIA Corporation"}),
                                         encoding="utf-8")
    structure = json.loads(Path("examples/m3_rebuild/hca_input.json").read_text(encoding="utf-8"))
    structure["company_name"] = "NVIDIA Corporation"
    (folder / shared_filing.M3_STRUCTURE).write_text(json.dumps(structure), encoding="utf-8")
    out = bo_fo.run_bo_fo(database=_database(tmp_path), folder=FOLDER, bo_backend=None, fo_backend=agent(),
                          fo_mode="scripted", output_root=tmp_path / "out", m3_version="v2")
    assert out["bo"]["status"] == "completed", out
    manifest = next((tmp_path / "out" / "m3_rebuild").glob("*/*/m3_rebuild_manifest.json"))
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    assert recorded["components"]["structure"]["status"] == "complete"
    assert recorded["evidence_context"]["corporate_document_id"]
    assert recorded["evidence_context"]["management_document_id"]  # the folder's 10-K was selected
    # The BO page shows the M3-v2 section with the submitted structure's simulation notice.
    page = next((tmp_path / "out" / "bo").glob("*/*/business_overview.html")).read_text(encoding="utf-8")
    assert "Corporate Structure &amp; Senior Management (M3-v2)" in page
    assert "SIMULATED EDUCATIONAL EXAMPLE" in page


def test_corporate_structure_json_is_found_by_its_declared_type(root):
    import json
    from pathlib import Path

    from app.services.acquisition import shared_filing

    folder = root / FOLDER
    shutil.copytree(NVDA, folder)
    assert shared_filing.m3_structure_path(FOLDER) is None
    example = Path("examples/m3_rebuild/hca_input.json").read_text(encoding="utf-8")
    (folder / "Simplified Corp Structure.json").write_text(example, encoding="utf-8")
    (folder / "notes.json").write_text(json.dumps({"document_type": "other"}), encoding="utf-8")
    assert shared_filing.m3_structure_path(FOLDER).name == "Simplified Corp Structure.json"
    (folder / "Another Structure.json").write_text(example, encoding="utf-8")
    with pytest.raises(shared_filing.SharedFilingError, match="more than one"):
        shared_filing.m3_structure_path(FOLDER)
    (folder / shared_filing.M3_STRUCTURE).write_text(example, encoding="utf-8")
    assert shared_filing.m3_structure_path(FOLDER).name == shared_filing.M3_STRUCTURE  # the fixed name wins


def test_workbench_lists_m3_v2_components_with_their_validation_outcome():
    from app.services.bo.m3_rebuild.adapters import unavailable_block
    from app.services.bo.workbench import _m3_v2_agents

    block = unavailable_block("C")
    block["m3_rebuild"]["structure"].update(status="complete", validation_status="passed", error=None)
    block["m3_rebuild"]["management"].update(
        status="partial", validation_status="partial", error=None,
        artifact={"validation": {"validator_7": {"decisions": [
            {"outcome": "accepted"}, {"outcome": "accepted"}, {"outcome": "unsupported"}]}},
            "limitations": ["1 individual evidence claims are unsupported."]})
    rows = {r["agent_id"]: r for r in _m3_v2_agents(block)}
    assert rows["m3_structure_conversion"]["status"] == "validated"
    assert rows["m3_management_assessment"]["status"] == "partially_validated"
    assert rows["m3_management_assessment"]["detail"] == "Validator 7: 2 accepted, 1 unsupported"
    assert _m3_v2_agents({"module": "M3", "m3_assessment_status": "validated"}) == []  # M3-v1 is unchanged
