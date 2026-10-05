from sqlalchemy import create_engine, inspect, text

from app.migrate import ADDITIONS, migrate
from app.models.base import init_db


def test_additive_migration_preserves_payloads_and_backfills_roles(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    init_db(engine)
    with engine.begin() as connection:
        # Recreate the pre-hardening shape by dropping only the new columns
        # from this isolated test database, then seed legacy data.
        for table, columns in ADDITIONS.items():
            for column in columns:
                connection.execute(
                    text(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
                )
        connection.execute(
            text(
                "INSERT INTO cases (case_id,status,created_at) VALUES ('C','open','2024-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO entities (entity_id,case_id,legal_name,aliases,tickers,entity_type,borrower_flag,guarantor_flag,source_refs) VALUES ('DAL','C','Delta','[]','[]','borrower',1,0,'[]')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO snapshots (case_id,snapshot_type,snapshot_version,schema_version,finalized,config_versions,payload,created_at) VALUES ('C','final_case',1,'1.0',1,'{}',:payload,'2024-01-01')"
            ),
            {"payload": '{"preserve":true}'},
        )
    assert migrate(engine)
    assert migrate(engine) == []
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT payload FROM snapshots")).scalar()
            == '{"preserve":true}'
        )
        assert (
            connection.execute(
                text("SELECT borrower_flag FROM case_entities WHERE case_id='C'")
            ).scalar()
            == 1
        )
        inspector = inspect(connection)
        for table, columns in ADDITIONS.items():
            assert set(columns) <= {c["name"] for c in inspector.get_columns(table)}
    engine.dispose()
