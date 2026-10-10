"""Additive SQLite migration for pre-hardening PoC databases.

Back up a database before invoking this module. No existing evidence, snapshot,
audit or review payload is rewritten.
"""

import argparse
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.models.base import init_db

ADDITIONS = {
    "facts": {
        "original_name": "TEXT",
        "mapping_version": "INTEGER",
        "mapping_hash": "TEXT",
        "mapping_status": "TEXT",
        "mapping_candidates": "JSON NOT NULL DEFAULT '[]'",
        "dimensions": "JSON NOT NULL DEFAULT '{}'",
        "xbrl_context_id": "TEXT",
        "inline_element_id": "TEXT",
        "sec_accession": "TEXT",
    },
    "fact_source_refs": {
        "xbrl_context_id": "TEXT",
        "inline_element_id": "TEXT",
        "presentation_role": "TEXT",
        "sec_accession": "TEXT",
    },
    "reconciliation_records": {
        "selected_fact_id": "TEXT",
        "selected_value": "FLOAT",
        "selection_reason": "TEXT",
        "selection_version": "INTEGER",
    },
    "metrics": {
        "evidence_quality": "TEXT NOT NULL DEFAULT 'unverified'",
        "review_required": "BOOLEAN NOT NULL DEFAULT 1",
    },
}

# Agentic credit-analysis tables (Milestone 1.2). These are whole NEW tables
# (not column additions), so the migration detects which are absent BEFORE
# ``init_db`` creates them and reports each as an explicit, observable change.
# No existing evidence/snapshot/audit/review payload is touched.
AGENTIC_TABLES = (
    "agentic_analysis_runs",
    "parameter_results",
    "agent_runs",
    "evidence_packets",
    "topic_conclusions",
    "challenge_findings",
    "candidate_structures",
    "candidate_feasibility",
    "risk_scores",
)

# Sector benchmarking tables (healthcare / medical devices extension). Whole new
# tables, created by ``init_db`` and reported like the agentic ones.
SECTOR_BENCHMARK_TABLES = (
    "sector_classifications",
    "industry_benchmark_comparisons",
)


def migrate(engine):
    # Which agentic tables are missing in the EXISTING schema (before init_db)?
    with engine.connect() as connection:
        before_tables = set(inspect(connection).get_table_names())
    new_agentic_tables = [
        t for t in (*AGENTIC_TABLES, *SECTOR_BENCHMARK_TABLES)
        if t not in before_tables
    ]

    init_db(engine)  # Creates CaseEntity + any tables absent from the old schema.
    changes = [f"table:{t}" for t in new_agentic_tables]
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, columns in ADDITIONS.items():
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(
                        text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
                    )
                    changes.append(f"{table}.{name}")
        # Retain legacy columns for compatibility, but use the association as
        # the authoritative source of case-specific roles from this point on.
        result = connection.execute(
            text("""INSERT OR IGNORE INTO case_entities
            (case_id, entity_id, role, borrower_flag, guarantor_flag)
            SELECT case_id, entity_id, entity_type, borrower_flag, guarantor_flag
            FROM entities WHERE case_id IS NOT NULL""")
        )
        if result.rowcount:
            changes.append(f"case_entities: {result.rowcount} associations backfilled")
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("Database does not exist; new databases do not need migration.")
    engine = create_engine(f"sqlite:///{args.database.resolve().as_posix()}")
    try:
        for change in migrate(engine):
            print(change)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
