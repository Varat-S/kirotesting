"""Output generation: FinalCaseSnapshot JSON first, then the PDF memo (M8).

This module implements Milestone 8 tasks 8.1 and 8.2. It enforces the headline
guarantee of the milestone (Req 17.2, design critical rule 15):

    *The JSON is the source of truth; the PDF is a pure rendering of it.*

Flow:

1. **JSON first (task 8.1 / Req 17.1).** :meth:`MemoReportGenerator.generate_json`
   reads the EXACT finalized :class:`~app.models.orm.Snapshot` row, validates its
   payload against the versioned JSON Schema, and assembles a canonical
   ``memo JSON`` document. The memo JSON is the FinalCaseSnapshot content (source
   references, facts, metrics, analysis, escalations, human reviews, final
   status, version metadata) plus a small ``output`` envelope that records the
   as-of date, evidence cutoff, unresolved exceptions, and the EXACT snapshot
   version + ``content_hash`` the output was produced from (Req 16.4 / 17.6).
   As-of date and evidence cutoff are resolved ONCE here from the referenced
   ``CanonicalEvidenceSnapshot`` and embedded in the memo JSON so the renderer
   never touches the database. Emits ``memo_generated`` and ``case_finalized``.

2. **PDF from JSON (task 8.2 / Req 17.2-17.7).** :meth:`render_html` renders a
   deterministic Jinja2 HTML document consuming ONLY the memo JSON -- no DB
   re-read, no LLM, and no content that is not present in the JSON. The HTML is
   the parity-checkable surface: every number and claim shown comes verbatim
   from the JSON. :meth:`render_pdf` converts that same HTML to PDF when an
   offline backend is installed; when none is available it reports so and the
   PDF-production test skips gracefully (the HTML artifact is always produced).

Determinism (Req 17.7): the memo JSON is serialized with the canonical JSON
helper (sorted keys) and the HTML is rendered from it with autoescaping and a
fixed section order, so re-rendering the same finalized snapshot yields
byte-identical HTML. Binary PDFs may embed engine timestamps, so re-render
equivalence is asserted on the HTML/structured surface, not on raw PDF bytes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import canonical_json, content_hash
from app.models.orm import Snapshot
from app.schemas.json_schema import validate_snapshot
from app.services.audit.log import ActorType, AuditLog, EventType

FINAL_SNAPSHOT_TYPE = "final_case"
EVIDENCE_SNAPSHOT_TYPE = "canonical_evidence"

# Memo-JSON schema version for the output envelope (distinct from the snapshot
# schema version). Bumped if the output document shape changes.
MEMO_OUTPUT_VERSION = "1.0"

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_MEMO_TEMPLATE = "memo.html.j2"

# The configured memo sections, in deterministic render order (Req 17.3). Each
# entry is ``(section_key, human_title)``; the template renders them in order.
MEMO_SECTIONS: tuple[tuple[str, str], ...] = (
    ("case_summary", "Case & Transaction Summary"),
    ("business_overview", "Business Overview"),
    ("financial_overview", "Financial Overview"),
    ("metrics_trends", "Key Metrics & Trends"),
    ("risk_register", "Risk Register"),
    ("mitigants", "Mitigants"),
    ("exceptions", "Exceptions / Missing Evidence"),
    ("recommendation", "Draft Recommendation"),
    ("sources_appendix", "Sources & Evidence Appendix"),
    ("review_history", "Review & Approval History"),
)


class PdfBackendUnavailableError(RuntimeError):
    """Raised when PDF production is requested but no offline backend exists."""


class OutputIntegrityError(RuntimeError):
    """Raised when the finalized snapshot fails its integrity check."""


@dataclass(frozen=True)
class MemoOutput:
    """Result of generating outputs for a finalized snapshot.

    ``memo_json`` is the canonical source-of-truth document; ``html`` is the
    deterministic rendering of it; ``json_path``/``html_path``/``pdf_path`` are
    populated only when the caller asked for the artifact to be written.
    """

    memo_json: dict[str, Any]
    canonical_json: str
    html: str
    snapshot_version: int
    content_hash: str
    json_path: Path | None = None
    html_path: Path | None = None
    pdf_path: Path | None = None


class MemoReportGenerator:
    """Generate the FinalCaseSnapshot JSON and render the memo from it."""

    def __init__(
        self,
        session: Session,
        *,
        audit: AuditLog | None = None,
        output_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        # Output root is configurable; defaults under the repo ``output/`` tree.
        # Tests pass a pytest ``tmp_path`` so nothing is written to the repo.
        self._output_root = (
            Path(output_root) if output_root is not None else Path("output")
        )
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    # -- 8.1: canonical JSON first --------------------------------------------

    def generate_json(
        self,
        *,
        case_id: str,
        snapshot_version: int,
        actor_id: str | None = None,
        write: bool = False,
    ) -> dict[str, Any]:
        """Produce the canonical memo JSON for a finalized snapshot (Req 17.1).

        The source of truth is the EXACT finalized ``Snapshot`` row identified by
        ``(case_id, snapshot_version)``. Its payload is re-validated against the
        versioned JSON Schema and its frozen ``content_hash`` re-verified so the
        output is provably linked to immutable, reproducible content (Req 16.4).
        Emits ``memo_generated`` and ``case_finalized``.
        """
        row = self._require_finalized(case_id, snapshot_version)
        self._verify_integrity(row)

        snapshot_payload = validate_snapshot(row.payload)
        as_of_date, evidence_cutoff = self._resolve_evidence_dates(
            case_id, snapshot_payload
        )
        unresolved = list(snapshot_payload.get("exceptions") or [])

        memo_json: dict[str, Any] = {
            "output_version": MEMO_OUTPUT_VERSION,
            "output_type": "credit_memo",
            # Output is linked to the EXACT finalized snapshot (Req 16.4 / 17.7).
            "source_snapshot": {
                "case_id": case_id,
                "snapshot_type": FINAL_SNAPSHOT_TYPE,
                "snapshot_version": row.snapshot_version,
                "schema_version": row.schema_version,
                "content_hash": row.content_hash,
                "supersedes_snapshot": snapshot_payload.get("supersedes_snapshot"),
            },
            # As-of / cutoff resolved once here and embedded so the renderer
            # never reads the database (Req 17.6).
            "as_of_date": as_of_date,
            "evidence_cutoff_timestamp": evidence_cutoff,
            "unresolved_exceptions": unresolved,
            "final_status": (snapshot_payload.get("recommendation") or {}).get(
                "status"
            ),
            # The full FinalCaseSnapshot content (source of truth).
            "final_case_snapshot": snapshot_payload,
        }
        _attach_agentic_provenance(memo_json, snapshot_payload)

        if self._audit is not None:
            linked = [f"snapshot:{FINAL_SNAPSHOT_TYPE}:{row.snapshot_version}"]
            self._audit.record(
                EventType.MEMO_GENERATED,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                actor_id=actor_id,
                after={
                    "snapshot_version": row.snapshot_version,
                    "content_hash": row.content_hash,
                    "output_version": MEMO_OUTPUT_VERSION,
                },
                reason="Canonical FinalCaseSnapshot memo JSON generated.",
                linked_objects=linked,
            )
            self._audit.record(
                EventType.CASE_FINALIZED,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                actor_id=actor_id,
                after={
                    "snapshot_version": row.snapshot_version,
                    "content_hash": row.content_hash,
                    "final_status": memo_json["final_status"],
                },
                reason="Case outputs finalized from the immutable FinalCaseSnapshot.",
                linked_objects=linked,
            )

        if write:
            self._write_json(case_id, row.snapshot_version, memo_json)
        return memo_json

    def generate_draft_json(
        self, *, case_id: str, snapshot_version: int
    ) -> dict[str, Any]:
        """Render an explicitly unapproved draft without emitting finalization events."""
        row = self._session.scalars(
            select(Snapshot).where(
                Snapshot.case_id == case_id,
                Snapshot.snapshot_type == FINAL_SNAPSHOT_TYPE,
                Snapshot.snapshot_version == snapshot_version,
            )
        ).one()
        if row.finalized:
            raise ValueError("Use generate_json for a finalized snapshot.")
        payload = validate_snapshot(row.payload)
        as_of, cutoff = self._resolve_evidence_dates(case_id, payload)
        memo_json = {
            "output_version": MEMO_OUTPUT_VERSION,
            "output_type": "credit_memo_draft",
            "source_snapshot": {
                "case_id": case_id,
                "snapshot_type": FINAL_SNAPSHOT_TYPE,
                "snapshot_version": row.snapshot_version,
                "schema_version": row.schema_version,
                "content_hash": content_hash(payload),
                "supersedes_snapshot": payload.get("supersedes_snapshot"),
            },
            "as_of_date": as_of,
            "evidence_cutoff_timestamp": cutoff,
            "unresolved_exceptions": payload.get("exceptions", []),
            "final_status": "draft",
            "final_case_snapshot": payload,
        }
        _attach_agentic_provenance(memo_json, payload)
        return memo_json

    # -- 8.2: deterministic rendering from the JSON ---------------------------

    def render_html(self, memo_json: dict[str, Any]) -> str:
        """Render the memo HTML from the memo JSON ONLY (Req 17.2-17.6).

        Consumes nothing but ``memo_json``: no DB access, no LLM, and no content
        absent from the JSON. The HTML is the deterministic, parity-checkable
        surface -- every number and claim it shows is read verbatim from the
        JSON. The exact same HTML string is what :meth:`render_pdf` converts.
        """
        template = self._env.get_template(_MEMO_TEMPLATE)
        context = self._build_context(memo_json)
        return template.render(**context)

    def render_pdf(self, memo_json: dict[str, Any]) -> bytes:
        """Convert the memo HTML to PDF bytes using an offline backend (Req 17.2).

        The PDF is produced from the EXACT HTML returned by :meth:`render_html`
        (so parity holds). If no offline HTML->PDF backend is installed, raises
        :class:`PdfBackendUnavailableError` and callers / tests skip gracefully.
        """
        html = self.render_html(memo_json)
        backend = detect_pdf_backend()
        if backend is None:
            raise PdfBackendUnavailableError(
                "No offline HTML->PDF backend is installed (tried weasyprint, "
                "xhtml2pdf). Install one to produce PDF; the deterministic HTML "
                "artifact is always available via render_html()."
            )
        return _render_pdf_with(backend, html)

    def generate(
        self,
        *,
        case_id: str,
        snapshot_version: int,
        actor_id: str | None = None,
        write: bool = False,
        produce_pdf: bool = False,
    ) -> MemoOutput:
        """Run the full output flow: JSON first, then render the memo.

        Returns a :class:`MemoOutput`. The PDF is produced only when
        ``produce_pdf`` is True AND an offline backend exists; otherwise the
        ``pdf_path`` stays ``None`` and the HTML artifact still holds all content.
        """
        memo_json = self.generate_json(
            case_id=case_id,
            snapshot_version=snapshot_version,
            actor_id=actor_id,
            write=write,
        )
        html = self.render_html(memo_json)
        version = memo_json["source_snapshot"]["snapshot_version"]
        chash = memo_json["source_snapshot"]["content_hash"]

        json_path = html_path = pdf_path = None
        if write:
            json_path = self._json_path(case_id, version)
            html_path = self._write_html(case_id, version, html)
            if produce_pdf and detect_pdf_backend() is not None:
                pdf_path = self._write_pdf(case_id, version, self.render_pdf(memo_json))

        return MemoOutput(
            memo_json=memo_json,
            canonical_json=canonical_json(memo_json),
            html=html,
            snapshot_version=version,
            content_hash=chash,
            json_path=json_path,
            html_path=html_path,
            pdf_path=pdf_path,
        )

    # -- context assembly (pure, JSON-only) -----------------------------------

    def _build_context(self, memo_json: dict[str, Any]) -> dict[str, Any]:
        """Shape the memo JSON into a render context without inventing content.

        Every value in the context is read directly from ``memo_json``. Claims
        carry their evidence references (``evidence_refs`` / ``evidence_ids``)
        from the analysis objects so the template can map each major claim back
        to an evidence/analysis object (Req 17.5).
        """
        snap = memo_json["final_case_snapshot"]
        return {
            "sections": MEMO_SECTIONS,
            "output_version": memo_json["output_version"],
            "source_snapshot": memo_json["source_snapshot"],
            "as_of_date": memo_json.get("as_of_date"),
            "evidence_cutoff_timestamp": memo_json.get("evidence_cutoff_timestamp"),
            "unresolved_exceptions": memo_json.get("unresolved_exceptions") or [],
            "final_status": memo_json.get("final_status"),
            "business_analysis": snap.get("business_analysis") or {},
            "financial_analysis": snap.get("financial_analysis") or {},
            "metrics": snap.get("metrics") or {},
            "benchmarks": snap.get("benchmarks") or {},
            "risks": snap.get("risks") or [],
            "mitigants": snap.get("mitigants") or [],
            "escalations": snap.get("escalations") or [],
            "human_reviews": snap.get("human_reviews") or [],
            "recommendation": snap.get("recommendation") or {},
            "evidence_snapshot_ref": snap.get("evidence_snapshot_ref") or {},
            "config_versions": snap.get("config_versions") or {},
            "metric_definition_versions": snap.get("metric_definition_versions") or {},
            "rule_versions": snap.get("rule_versions") or {},
            "prompt_model_versions": snap.get("prompt_model_versions") or {},
        }

    # -- lookups / integrity --------------------------------------------------

    def _require_finalized(self, case_id: str, snapshot_version: int) -> Snapshot:
        stmt = select(Snapshot).where(
            Snapshot.case_id == case_id,
            Snapshot.snapshot_type == FINAL_SNAPSHOT_TYPE,
            Snapshot.snapshot_version == snapshot_version,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            raise ValueError(
                f"No FinalCaseSnapshot v{snapshot_version} for case {case_id!r}; "
                "outputs can only be generated from an existing finalized snapshot."
            )
        if not row.finalized:
            raise ValueError(
                f"FinalCaseSnapshot v{snapshot_version} for case {case_id!r} is not "
                "finalized; outputs are rendered only from a finalized snapshot."
            )
        return row

    def _verify_integrity(self, row: Snapshot) -> None:
        from app.core.hashing import content_hash

        if row.content_hash is None or content_hash(row.payload) != row.content_hash:
            raise OutputIntegrityError(
                f"FinalCaseSnapshot v{row.snapshot_version} for case {row.case_id!r} "
                "failed its integrity check; refusing to render stale output."
            )

    def _resolve_evidence_dates(
        self, case_id: str, snapshot_payload: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        """Read as-of/cutoff from the referenced CanonicalEvidenceSnapshot.

        Done ONCE at JSON-generation time so the values are embedded in the memo
        JSON and the renderer never touches the database (Req 17.6). Returns the
        JSON-serializable (ISO string) forms already present in the evidence
        snapshot payload.
        """
        ref = snapshot_payload.get("evidence_snapshot_ref") or {}
        version = ref.get("snapshot_version")
        if version is None:
            return None, None
        stmt = select(Snapshot).where(
            Snapshot.case_id == case_id,
            Snapshot.snapshot_type == EVIDENCE_SNAPSHOT_TYPE,
            Snapshot.snapshot_version == version,
        )
        evidence = self._session.execute(stmt).scalar_one_or_none()
        if evidence is None:
            return None, None
        payload = evidence.payload or {}
        return payload.get("as_of_date"), payload.get("evidence_cutoff_timestamp")

    # -- writers (configurable output root) -----------------------------------

    def _json_path(self, case_id: str, version: int) -> Path:
        return self._output_root / "json" / f"{case_id}_v{version}.json"

    def _html_path(self, case_id: str, version: int) -> Path:
        return self._output_root / "pdf" / f"{case_id}_v{version}.html"

    def _pdf_path(self, case_id: str, version: int) -> Path:
        return self._output_root / "pdf" / f"{case_id}_v{version}.pdf"

    def _write_json(
        self, case_id: str, version: int, memo_json: dict[str, Any]
    ) -> Path:
        path = self._json_path(case_id, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(memo_json), encoding="utf-8")
        return path

    def _write_html(self, case_id: str, version: int, html: str) -> Path:
        path = self._html_path(case_id, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return path

    def _write_pdf(self, case_id: str, version: int, pdf: bytes) -> Path:
        path = self._pdf_path(case_id, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf)
        return path


# -- PDF backend detection (offline, optional) --------------------------------


def _attach_agentic_provenance(memo_json: dict[str, Any], payload: dict[str, Any]) -> None:
    """Surface the agentic provenance block at the top of the memo JSON.

    The agentic path records the accepted ``analysis_run_id``, scores and
    accepted parameter ids under ``business_analysis.agentic``. It is copied
    (not moved) to a top-level ``agentic`` key so a reader of the memo can find
    the exact accepted run without knowing that nesting. Legacy memos have no
    such block and gain no key, so their JSON is unchanged.
    """
    agentic = (payload.get("business_analysis") or {}).get("agentic")
    if agentic:
        memo_json["agentic"] = agentic


def detect_pdf_backend() -> str | None:
    """Return the name of an available offline HTML->PDF backend, or ``None``.

    Tried in order of preference. All are optional; when none is installed the
    renderer still produces the deterministic HTML artifact and PDF-production
    tests skip gracefully (per M8 guidance; no system dependency is forced).
    """
    import importlib.util

    for name in ("weasyprint", "xhtml2pdf"):
        if importlib.util.find_spec(name) is not None:
            try:
                import importlib

                importlib.import_module(name)
                return name
            except (ImportError, OSError):
                continue
    return None


def _render_pdf_with(backend: str, html: str) -> bytes:
    if backend == "weasyprint":  # pragma: no cover - exercised only when installed
        try:
            import weasyprint
        except (ImportError, OSError) as exc:
            raise PdfBackendUnavailableError(
                "WeasyPrint native libraries are unavailable."
            ) from exc

        return weasyprint.HTML(string=html).write_pdf()
    if backend == "xhtml2pdf":  # pragma: no cover - exercised only when installed
        import io

        from xhtml2pdf import pisa

        buffer = io.BytesIO()
        pisa.CreatePDF(src=html, dest=buffer)
        return buffer.getvalue()
    raise PdfBackendUnavailableError(f"Unknown PDF backend {backend!r}.")
