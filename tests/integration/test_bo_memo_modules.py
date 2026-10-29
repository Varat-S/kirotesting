"""The BO memo lays out each roster-v3 module's governed output (M2-M4).

Each module's panel is attached to the memo section of the same code: status
and gaps for every module, the validated M3 corporate-structure assessment, and
the assembled M4 analysis (debt specialist's four deliverables + validator).
Roster v2 memos are unchanged. Offline, deterministic, no paid model.
"""

from __future__ import annotations

from tests import bo_helpers as h
from tests.integration.test_bo_m4_dual_specialist import _run as run_with_debt
from tests.integration.test_bo_v3_complete import _run as run_v3, _script as v3_script


def _html(result) -> str:
    return result.output_paths["business_overview.html"].read_text(encoding="utf-8")


def _modules(result) -> dict:
    return {s["code"]: s["module"] for s in result.memo["sections"] if s.get("module")}


def test_v3_memo_attaches_a_panel_to_each_of_m2_to_m4(db_session, tmp_path):
    result, _ = run_with_debt(db_session, tmp_path)
    modules = _modules(result)

    assert set(modules) == {"M2", "M3", "M4"}
    for code, module in modules.items():
        assert module["status"] == result.memo["module_conclusions"][code]["status"]
    html = _html(result)
    for code in ("M2", "M3", "M4"):
        assert f'id="module-{code}"' in html


def test_v3_memo_renders_the_assembled_m4_debt_deliverables(db_session, tmp_path):
    result, _ = run_with_debt(db_session, tmp_path)
    m4 = _modules(result)["M4"]["m4"]

    assert m4["debt_run"] and m4["debt_validated"]
    assert m4["d1"]["creditor_priority_framework"]["consolidated_total_indebtedness_m"] == 46492.0
    assert m4["checks"] and all(c["passed"] for c in m4["checks"])
    html = _html(result)
    for heading in ("Debt structure credit conclusion", "Debt and creditor priority",
                    "Refinancing and maturity", "Debt utilisation and leverage impact",
                    "Synthesis matrix", "Debt-structure validator"):
        assert heading in html
    assert "46,492" in html


def test_v3_memo_says_when_the_debt_specialist_did_not_run(db_session, tmp_path):
    result, _ = run_with_debt(db_session, tmp_path, enable=False)

    assert _modules(result)["M4"]["m4"] == {"debt_run": False}
    html = _html(result)
    assert "debt-structure specialist was not run" in html
    assert "Debt and creditor priority" not in html


def test_v2_memo_has_no_module_panels(db_session, tmp_path):
    result, _ = h.run(db_session, tmp_path)

    assert not _modules(result)
    assert 'class="module"' not in _html(result)
