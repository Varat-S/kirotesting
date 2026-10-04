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


def migrate(engine):
    init_db(engine)  # Creates CaseEntity and any tables absent from the old schema.
    changes = []
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
