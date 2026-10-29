"""Launch a local browser preview, optionally seeding synthetic demo cases."""

import argparse
import os
from pathlib import Path

from app.models.base import create_engine_and_session, init_db
from app.models.orm import Case
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.pipeline.default_case import prepare_default_case


def seed_demo(database: Path, output: Path) -> None:
    """Run three illustrative source packages once; never record human approvals."""
    package = SourcePackage.load(
        Path(__file__).resolve().parents[1] / "examples/synthetic-case/package.json"
    )
    missing = package.model_copy(
        update={
            "sources": [
                s for s in package.sources if "financial_statements" not in s.tags
            ]
        }
    )
    pdf = next(s for s in package.sources if s.parser == "pdf_table")
    conflicting_source = pdf.model_copy(
        update={
            "filename": "conflict.csv",
            "path": None,
            "data": b"Line item,2024\nOperating Revenue,4000\n",
            "parser": "csv",
        }
    )
    conflict = package.model_copy(
        update={"sources": [*package.sources, conflicting_source]}
    )
    database.parent.mkdir(parents=True, exist_ok=True)
    engine, factory = create_engine_and_session(
        f"sqlite:///{database.resolve().as_posix()}"
    )
    init_db(engine)
    try:
        for case_id, source_package in [
            ("SYNTHETIC_2024", package),
            ("SYNTHETIC_MISSING", missing),
            ("SYNTHETIC_CONFLICT", conflict),
        ]:
            with factory() as session:
                if session.get(Case, case_id) is not None:
                    continue
                CreditMemoPipeline(
                    session, data_root=database.parent, output_root=output
                ).run_case(case_id, package=source_package)
                session.commit()
    finally:
        engine.dispose()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Local browser preview of saved credit memo cases."
    )
    parser.add_argument("--database", type=Path, default=Path("data/browser_demo.db"))
    parser.add_argument("--output", type=Path, default=Path("output/browser_demo"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--seed-default",
        action="store_true",
        help="Prepare the user's three Delta PDFs for evidence review, without LLM execution.",
    )
    parser.add_argument(
        "--seed-demo",
        action="store_true",
        help="Create full, missing-data and conflicting-source synthetic drafts.",
    )
    args = parser.parse_args(argv)
    args.database.parent.mkdir(parents=True, exist_ok=True)
    if args.seed_demo:
        seed_demo(args.database, args.output)
    if args.seed_default:
        engine, factory = create_engine_and_session(
            f"sqlite:///{args.database.resolve().as_posix()}"
        )
        init_db(engine)
        try:
            with factory() as session:
                prepare_default_case(
                    session, data_root=args.database.parent, output_root=args.output
                )
                session.commit()
        finally:
            engine.dispose()
    os.environ["DATABASE_URL"] = f"sqlite:///{args.database.resolve().as_posix()}"
    os.environ["DATA_DIR"] = str(args.database.parent.resolve())
    os.environ["OUTPUT_DIR"] = str(args.output.resolve())
    os.environ["DEBUG"] = "false"
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
