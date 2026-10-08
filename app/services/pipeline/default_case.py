"""The user's three Delta PDFs form the default local evidence-review case."""

import json
from hashlib import sha256
from pathlib import Path

from app.core.hashing import canonical_json
from app.models.orm import Case, EvidencePreview
from app.services.pipeline.artifacts import StageRecorder, verified_artifacts
from app.services.pipeline.coverage import ocr_reference_checks, reference_checks
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline

DEFAULT_CASE_ID = "DELTA_2025_OCR_REVIEW"
DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "examples/delta-2025"


def default_package():
    hashes = json.loads((DEFAULT_ROOT / "sha256.json").read_text(encoding="utf-8"))
    package = SourcePackage.load(DEFAULT_ROOT / "package.json")
    if set(hashes) != {s.filename for s in package.sources}:
        raise ValueError("Default Delta package and file hashes do not agree.")
    for source in package.sources:
        if (
            sha256(Path(source.path).read_bytes()).hexdigest()
            != hashes[source.filename]
        ):
            raise ValueError(f"Default Delta file hash mismatch: {source.filename}")
    return package


def prepare_default_case(session, *, data_root, output_root, case_id=DEFAULT_CASE_ID):
    existing = session.get(EvidencePreview, (case_id, 1))
    if existing is not None:
        return existing
    if session.get(Case, case_id) is not None:
        raise ValueError("Default case ID already exists without an evidence preview.")
    package = default_package()
    with session.begin_nested():
        result = CreditMemoPipeline(
            session, data_root=data_root, output_root=output_root
        ).prepare_case(case_id, package=package)
        reference = json.loads((DEFAULT_ROOT / "reference_values.json").read_text())
        ocr_reference = json.loads((DEFAULT_ROOT / "ocr_reference.json").read_text())
        checks = reference_checks(result.as_payload(), reference)
        captures = [
            json.loads(a.payload_json)
            for a in verified_artifacts(
                session, case_id, result.evidence_snapshot.snapshot_version
            )
            if a.name.startswith("document_")
        ]
        scanned = next(
            (c for c in captures if any(p["method"] == "ocr" for p in c["pages"])),
            None,
        )
        if scanned is None:
            # The image-only supplement needs OCR; without an engine no OCR page
            # exists. Fail with a clear, actionable message rather than letting a
            # bare StopIteration surface as an opaque RuntimeError.
            raise ValueError(
                "The default Delta case requires OCR for its image-only "
                "non-GAAP supplement, but no OCR page was produced. Install "
                "Tesseract and the project's 'ocr' extra, or use Windows OCR."
            )
        checks["ocr_recognition"] = ocr_reference_checks(scanned, ocr_reference)
        checks["document_coverage"] = [
            {"document_id": c["document_id"], **c["coverage"]} for c in captures
        ]
        StageRecorder(
            session, case_id, result.evidence_snapshot.snapshot_version
        ).record("reference_checks", checks)
        row = EvidencePreview(
            case_id=case_id,
            evidence_version=result.evidence_snapshot.snapshot_version,
            payload=json.loads(canonical_json(result.as_payload())),
        )
        session.add(row)
        session.flush()
    return row
