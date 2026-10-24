"""Fix 1: `run-bo-case --enable-m4-debt-specialist` CLI-to-runner integration.

Asserts the flag defaults to false (backward compatible), flows from the CLI
through to the runner, and that the effective flag is recorded in run metadata
and reported in the summary. Offline, deterministic, no network / paid model.
"""

from __future__ import annotations

import json

import pytest

from app.cli import main
from app.models.base import create_engine_and_session
from app.models.orm import AgenticAnalysisRun as RunRow
from tests.integration.test_bo_cli_workbench import _package
from tests.integration.test_bo_v3_complete import _script as v3_script
from tests.fixtures.hca_module4 import hca_module4_output


def _write_v3_script(tmp_path, *, with_debt: bool):
    script = v3_script()
    if with_debt:
        script["m4_debt"] = hca_module4_output()
    path = tmp_path / "v3script.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    return path


def _cli(tmp_path, capsys, *args):
    main(["--database", str(tmp_path / "case.db"), "--output", str(tmp_path / "out"),
          *args])
    return json.loads(capsys.readouterr().out)


def _recorded_flag(tmp_path, run_id):
    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'case.db'}")
    try:
        with factory() as session:
            row = session.get(RunRow, run_id)
            return row.model_configuration
    finally:
        engine.dispose()


def test_flag_defaults_false_and_is_recorded(tmp_path, capsys):
    summary = _cli(tmp_path, capsys, "run-bo-case", "POC_V3",
                   "--package", str(_package(tmp_path)),
                   "--responses", str(_write_v3_script(tmp_path, with_debt=False)),
                   "--roster", "v3")
    assert summary["m4_debt_specialist_requested"] is False
    assert summary["m4_debt_specialist_enabled"] is False
    cfg = _recorded_flag(tmp_path, summary["analysis_run_id"])
    assert cfg["m4_debt_specialist_requested"] is False
    assert cfg["m4_debt_specialist_enabled"] is False


def test_flag_flows_cli_to_runner_and_takes_effect_on_v3(tmp_path, capsys):
    summary = _cli(tmp_path, capsys, "run-bo-case", "POC_V3D",
                   "--package", str(_package(tmp_path)),
                   "--responses", str(_write_v3_script(tmp_path, with_debt=True)),
                   "--roster", "v3", "--enable-m4-debt-specialist")
    assert summary["m4_debt_specialist_requested"] is True
    assert summary["m4_debt_specialist_enabled"] is True
    cfg = _recorded_flag(tmp_path, summary["analysis_run_id"])
    assert cfg["m4_debt_specialist_requested"] is True
    assert cfg["m4_debt_specialist_enabled"] is True


def test_flag_requested_but_v2_roster_does_not_take_effect(tmp_path, capsys):
    # On v2 the debt specialist is not part of the orchestrated roster, so the
    # flag is requested but NOT effective; both are recorded distinctly.
    from tests.integration.test_bo_cli_workbench import _script as v2_script
    summary = _cli(tmp_path, capsys, "run-bo-case", "POC_V2D",
                   "--package", str(_package(tmp_path)),
                   "--responses", str(v2_script(tmp_path)),
                   "--enable-m4-debt-specialist")  # default roster v2
    assert summary["m4_debt_specialist_requested"] is True
    assert summary["m4_debt_specialist_enabled"] is False
    cfg = _recorded_flag(tmp_path, summary["analysis_run_id"])
    assert cfg["m4_debt_specialist_requested"] is True
    assert cfg["m4_debt_specialist_enabled"] is False
