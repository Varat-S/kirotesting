"""`run-bo-case` always runs the v3 roster with the Module 4 debt specialist.

There is no roster or debt-specialist flag: the CLI passes both through to the
runner, and the effective flag is recorded in run metadata and reported in the
summary. Offline, deterministic, no network / paid model.
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


def _write_v3_script(tmp_path):
    script = v3_script()
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


def test_v3_with_the_debt_specialist_runs_without_any_flag(tmp_path, capsys):
    summary = _cli(tmp_path, capsys, "run-bo-case", "POC_V3D",
                   "--package", str(_package(tmp_path)),
                   "--responses", str(_write_v3_script(tmp_path)))
    assert summary["bo_roster"] == "v3"
    assert summary["m4_debt_specialist_enabled"] is True
    cfg = _recorded_flag(tmp_path, summary["analysis_run_id"])
    assert cfg["m4_debt_specialist_requested"] is True
    assert cfg["m4_debt_specialist_enabled"] is True


@pytest.mark.parametrize("flag", [["--roster", "v2"], ["--enable-m4-debt-specialist"]])
def test_the_removed_flags_are_refused(tmp_path, capsys, flag):
    with pytest.raises(SystemExit):
        main(["--database", str(tmp_path / "case.db"), "run-bo-case", "POC",
              "--package", "p.json", *flag])
