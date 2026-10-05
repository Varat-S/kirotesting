import hashlib
import json

import pytest
from sqlalchemy import create_engine, text

from app.migrate import migrate
from app.models.base import init_db
from app.schemas.json_schema import (
    get_json_schema,
    validate_snapshot,
    SchemaValidationError,
)
from app.schemas.snapshots import CanonicalEvidenceSnapshot


def test_sec_migration_defaults_preserves_frozen_legacy_bytes_and_hash(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    init_db(engine)
    original = '{"schema_version":"1.0","snapshot_type":"final_case","evidence_snapshot_ref":{"case_id":"OLD","snapshot_version":1}}'
    digest = hashlib.sha256(original.encode()).hexdigest()
    assert validate_snapshot(json.loads(original))
    with engine.begin() as connection:
        for table, columns in {
            "facts": [
                "dimensions",
                "xbrl_context_id",
                "inline_element_id",
                "sec_accession",
            ],
            "fact_source_refs": [
                "xbrl_context_id",
                "inline_element_id",
                "sec_accession",
                "presentation_role",
            ],
        }.items():
            for column in columns:
                connection.execute(
                    text(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
                )
        connection.execute(
            text(
                "INSERT INTO cases (case_id,status,created_at) VALUES ('OLD','open','2024-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO facts (fact_id,case_id,name,mapping_candidates,status,restated,created_at) VALUES ('OLD-FACT','OLD','revenue','[]','unverified',0,'2024-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO fact_source_refs (fact_id,document_id) VALUES ('OLD-FACT','OLD-DOC')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO snapshots (case_id,snapshot_type,snapshot_version,schema_version,finalized,config_versions,payload,content_hash,created_at) VALUES ('OLD','final_case',1,'1.0',1,'{}',:payload,:hash,'2024-01-01')"
            ),
            {"payload": original, "hash": digest},
        )
    assert len(migrate(engine)) == 8
    assert migrate(engine) == []
    with engine.connect() as connection:
        payload, stored_hash, version = connection.execute(
            text("SELECT payload,content_hash,schema_version FROM snapshots")
        ).one()
        assert payload == original and stored_hash == digest and version == "1.0"
        assert hashlib.sha256(payload.encode()).hexdigest() == stored_hash
        assert connection.execute(
            text("SELECT dimensions,xbrl_context_id FROM facts")
        ).one() == ("{}", None)
        assert (
            connection.execute(
                text("SELECT inline_element_id FROM fact_source_refs")
            ).scalar()
            is None
        )
    engine.dispose()


def test_schema_versions_do_not_mutate_old_definitions():
    assert (
        "sec_filings" not in get_json_schema("canonical_evidence", "1.0")["properties"]
    )
    assert "sec_filings" in get_json_schema("canonical_evidence", "1.1")["properties"]
    snapshot = CanonicalEvidenceSnapshot(
        case_id="NEW",
        facts=[
            {
                "dimensions": {"Axis": "Member"},
                "xbrl_context_id": "C",
                "inline_element_id": "F",
            }
        ],
    )
    assert validate_snapshot(snapshot)["schema_version"] == "1.1"
    bad = snapshot.model_dump(mode="json")
    bad["facts"][0]["dimensions"] = {"Axis": 5}
    with pytest.raises(SchemaValidationError):
        validate_snapshot(bad)
