"""Task 11 — real SEC source integration + authoritative metric binding.

These tests connect the Module-1 retrieval and numerical-corroboration machinery
to the REAL NVIDIA FY2026 10-K fixture (``examples/sec/nvda-2026/``) and to the
AUTHORITATIVE persisted ``Fact``/``Metric`` records, rather than to caller-
supplied metadata or a synthetic corpus. Three things are proven:

1. **Real retrieval.** The genuine 10-K narrative (loaded through the existing
   SEC pipeline, used read-only) is turned into a searchable corpus by
   :func:`build_corpus_from_sec_narrative`, with stable passage ids, verbatim
   text, HTML-native section locations and NO fabricated PDF pages, and lexical
   search returns the right real passages. The post-cutoff proxy (DEF 14A) is
   excluded; the 13 in-scope sources are admitted.

2. **Authoritative metric binding.** :class:`AuthoritativeMetricResolver`
   resolves a requested id ONLY against a real, approved persisted record and
   FAILS CLOSED for unknown / cross-case / wrong-entity / unverified /
   value-less / unsourced records. The real pipeline facts (status
   ``unverified``) are correctly refused; a reconciled ``verified`` fact with a
   persisted source is accepted.

The main ``CreditMemoPipeline`` runner is NOT modified; it is only invoked to
produce the authoritative evidence these additive services bind to.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.base import create_engine_and_session, init_db
from app.models.orm import Entity, Fact, FactSourceRef
from app.schemas.enums import ExtractionMethod, FactStatus
from app.schemas.evidence import EvidenceSearchIntent, EvidenceSearchRequest
from app.services.analysis.evidence_retrieval import (
    EvidenceSearchService,
    build_corpus_from_sec_narrative,
)
from app.services.analysis.metric_resolver import AuthoritativeMetricResolver
from app.services.extraction.sec.bundle import SecFilingBundle
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.pipeline.sec import sec_source_package

FIXTURE = Path(__file__).resolve().parents[2] / "examples/sec/nvda-2026/bundle.json"
CUTOFF = datetime(2026, 3, 1, tzinfo=timezone.utc)
CASE = "NVDA_2026"


def _run_real_pipeline(session, tmp_path):
    """Run the real NVIDIA fixture through the existing pipeline (read-only use).

    Returns the pipeline result; its ``evidence_snapshot`` carries the
    authoritative facts + the real narrative passages Task 11 binds to."""
    source = SecFilingBundle.load(FIXTURE)
    package = sec_source_package(source, cutoff=CUTOFF)
    result = CreditMemoPipeline(
        session, data_root=tmp_path / "data", output_root=tmp_path / "output"
    ).run_case(CASE, package=package)
    return source, result


# ===========================================================================
# 1. REAL NVIDIA NARRATIVE RETRIEVAL
# ===========================================================================


def test_real_nvda_cutoff_admits_in_scope_and_rejects_proxy(db_session, tmp_path):
    """The 2026-03-01 cutoff admits the 13 in-scope filing files and rejects the
    later DEF 14A proxy -- the retrieval corpus is built only from admitted,
    in-scope evidence (never the excluded proxy)."""
    _, result = _run_real_pipeline(db_session, tmp_path)
    assert len(result.admitted_source_hashes) == 13
    assert len(result.rejected_sources) == 1
    assert result.rejected_sources[0]["form"] == "DEF 14A"
    # The proxy accession never appears in the admitted narrative evidence.
    rejected_accession = result.rejected_sources[0].get("accession")
    assert rejected_accession is not None
    assert not any(
        p["sec_accession"] == rejected_accession
        for p in result.evidence_snapshot.narrative_evidence
    )


def test_real_nvda_narrative_corpus_is_searchable_without_fabricated_pages(
    db_session, tmp_path
):
    """The genuine 10-K narrative becomes a searchable corpus: verbatim text,
    HTML-native section locations, NO fabricated PDF page numbers, and lexical
    search returns real passages whose text is present in the source."""
    _, result = _run_real_pipeline(db_session, tmp_path)
    narrative = result.evidence_snapshot.narrative_evidence
    primary_id = result.evidence_snapshot.sec_filings[0]["primary_document_id"]
    primary_passages = [p for p in narrative if p["document_id"] == primary_id]
    assert primary_passages, "the real 10-K produced narrative passages"

    document_hash = result.evidence_snapshot.sec_filings[0].get("primary_document_hash")
    corpus = build_corpus_from_sec_narrative(
        primary_passages, document_id=primary_id, document_hash=document_hash
    )
    assert corpus.passages
    # No fabricated PDF pages for an HTML/inline-XBRL filing; HTML-native
    # section location carried instead.
    assert all(p.pdf_page is None for p in corpus.passages)
    assert all(p.extraction_method is ExtractionMethod.INLINE_XBRL
               for p in corpus.passages)
    assert any(p.section for p in corpus.passages)
    # Text copied verbatim from the source passages (never rewritten).
    source_texts = {p["text"] for p in primary_passages}
    assert all(p.text in source_texts for p in corpus.passages)

    # Stable passage ids: rebuilding from the same inputs reproduces them exactly.
    rebuilt = build_corpus_from_sec_narrative(
        primary_passages, document_id=primary_id, document_hash=document_hash
    )
    assert [p.passage_id for p in corpus.passages] == [
        p.passage_id for p in rebuilt.passages
    ]

    # Lexical retrieval over the real corpus returns real passages.
    service = EvidenceSearchService(corpus)
    results = service.search(EvidenceSearchRequest(
        queries=["competition and competitors in the markets we serve",
                 "supply and manufacturing capacity constraints"],
        intent=EvidenceSearchIntent.GENERAL_INVESTIGATION,
        permitted_document_ids=[primary_id],
        max_results=5,
    ))
    assert len(results) == 2
    hit = next((r for r in results
                if r.outcome.value == "matches_found" and r.ranked_passages), None)
    assert hit is not None, "a real narrative query matched real passages"
    top = hit.ranked_passages[0].passage
    assert top.document_id == primary_id and top.pdf_page is None
    assert top.text.strip()  # verbatim narrative text


def test_real_nvda_search_honours_permitted_documents(db_session, tmp_path):
    """A query restricted to a document absent from the corpus returns a
    source-not-available outcome -- retrieval never widens beyond permitted
    sources, and a no-source result is explicit (not silent)."""
    _, result = _run_real_pipeline(db_session, tmp_path)
    primary_id = result.evidence_snapshot.sec_filings[0]["primary_document_id"]
    narrative = [p for p in result.evidence_snapshot.narrative_evidence
                 if p["document_id"] == primary_id]
    corpus = build_corpus_from_sec_narrative(narrative, document_id=primary_id)
    service = EvidenceSearchService(corpus)
    results = service.search(EvidenceSearchRequest(
        queries=["anything"],
        intent=EvidenceSearchIntent.GENERAL_INVESTIGATION,
        permitted_document_ids=["not-a-real-document"],
        max_results=3,
    ))
    assert results[0].outcome.value == "source_not_available"
    assert results[0].ranked_passages == []


# ===========================================================================
# 2. AUTHORITATIVE METRIC RESOLVER (persisted Fact/Metric binding)
# ===========================================================================


def _persist_verified_fact(session, *, fact_id, case_id, entity_id, value,
                           concept="us-gaap:Revenues", with_source=True,
                           status=FactStatus.VERIFIED.value):
    """Persist an authoritative, reconciled fact as it would exist post-review."""
    session.add(Fact(
        fact_id=fact_id, case_id=case_id, name="revenue",
        normalized_value=value, normalized_unit="USD_million",
        currency="USD", scale="millions", fiscal_year=2026, period_type="FY",
        period_end=date(2026, 1, 25), accounting_basis="GAAP",
        entity_id=entity_id, taxonomy_concept=concept, status=status,
        created_by="reconciliation@1.0.0",
    ))
    if with_source:
        session.add(FactSourceRef(
            fact_id=fact_id, document_id="nvda-primary", page=None,
            taxonomy_concept=concept, sec_accession="0001045810-26-000021",
        ))
    session.flush()


def _resolver_env(session):
    """A case + reporting entity to bind authoritative facts to."""
    from app.models.orm import Case

    session.add(Case(case_id=CASE, status="open"))
    session.add(Entity(entity_id="nvda", case_id=CASE, legal_name="NVIDIA CORP",
                       entity_type="borrower", borrower_flag=True))
    session.add(Entity(entity_id="other-co", case_id="OTHER",
                       legal_name="Other Inc", entity_type="borrower"))
    session.flush()


def test_resolver_accepts_only_approved_authoritative_fact(db_session):
    """A requested id resolves to a usable EvidenceItem ONLY when a real,
    verified, sourced, same-case, same-entity fact backs it -- and the item
    carries the record's TRUSTED concept/entity/period/scale/currency/basis."""
    _resolver_env(db_session)
    _persist_verified_fact(db_session, fact_id="f-rev-2026", case_id=CASE,
                           entity_id="nvda", value=215938)
    resolver = AuthoritativeMetricResolver(db_session)
    resolution = resolver.resolve(
        ["f-rev-2026"], case_id=CASE, reporting_entity_id="nvda")

    assert resolution.usable_ids == {"f-rev-2026"}
    item = resolution.items[0]
    assert item.evidence_id == "f-rev-2026"
    assert item.value == 215938
    assert item.concept == "us-gaap:Revenues"  # trusted record metadata
    assert item.entity == "nvda"
    assert item.scale == "millions" and item.currency == "USD"
    assert item.accounting_basis == "GAAP"
    assert item.period == "2026-01-25"
    assert resolution.rejected() == []


def test_resolver_rejects_unknown_cross_case_wrong_entity_unsourced_unverified(
    db_session,
):
    """Fail-closed on every untrusted shape: an unknown id, a cross-case fact, a
    wrong-entity fact, an unverified fact, a value-less fact, and an unsourced
    fact. None of these may corroborate a premise."""
    _resolver_env(db_session)
    # approved baseline (same case / entity / verified / sourced)
    _persist_verified_fact(db_session, fact_id="ok", case_id=CASE,
                           entity_id="nvda", value=215938)
    # cross-case fact
    _persist_verified_fact(db_session, fact_id="cross-case", case_id="OTHER",
                           entity_id="nvda", value=1)
    # wrong-entity fact (belongs to a different reporting entity)
    _persist_verified_fact(db_session, fact_id="wrong-entity", case_id=CASE,
                           entity_id="other-co", value=1)
    # unverified fact (the real pipeline's default status)
    _persist_verified_fact(db_session, fact_id="unverified", case_id=CASE,
                           entity_id="nvda", value=1,
                           status=FactStatus.UNVERIFIED.value)
    # conflicting fact
    _persist_verified_fact(db_session, fact_id="conflicting", case_id=CASE,
                           entity_id="nvda", value=1,
                           status=FactStatus.CONFLICTING.value)
    # value-less fact
    _persist_verified_fact(db_session, fact_id="valueless", case_id=CASE,
                           entity_id="nvda", value=None)
    # verified but UNSOURCED fact (no FactSourceRef -> untraceable)
    _persist_verified_fact(db_session, fact_id="unsourced", case_id=CASE,
                           entity_id="nvda", value=1, with_source=False)

    resolver = AuthoritativeMetricResolver(db_session)
    requested = ["ok", "unknown-id", "cross-case", "wrong-entity", "unverified",
                 "conflicting", "valueless", "unsourced"]
    resolution = resolver.resolve(
        requested, case_id=CASE, reporting_entity_id="nvda")

    assert resolution.usable_ids == {"ok"}
    rejected = {r.requested_id for r in resolution.rejected()}
    assert rejected == {"unknown-id", "cross-case", "wrong-entity", "unverified",
                        "conflicting", "valueless", "unsourced"}
    # Each rejection carries an explicit, specific reason (never silent).
    reasons = {r.requested_id: r.reasons for r in resolution.rejected()}
    assert any("no Fact or Metric" in m for m in reasons["unknown-id"])
    assert any("case" in m for m in reasons["cross-case"])
    assert any("entity" in m for m in reasons["wrong-entity"])
    assert any("not verified" in m for m in reasons["unverified"])
    assert any("not verified" in m for m in reasons["conflicting"])
    assert any("no normalized value" in m for m in reasons["valueless"])
    assert any("no persisted source" in m for m in reasons["unsourced"])


def test_resolver_refuses_real_pipeline_unverified_facts(db_session, tmp_path):
    """The REAL persisted NVIDIA facts are status ``unverified`` straight from
    extraction. The resolver refuses them (fail-closed): extraction is not
    reconciliation, so an unreconciled real fact cannot corroborate a premise.
    This proves the gate is genuine, not merely passing synthetic rows."""
    _, result = _run_real_pipeline(db_session, tmp_path)
    # A real FY2026 revenue fact exists and is persisted.
    real = db_session.scalars(
        select(Fact).where(
            Fact.case_id == CASE,
            Fact.taxonomy_concept == "us-gaap:Revenues",
            Fact.fiscal_year == 2026,
            Fact.period_type == "FY",
        )
    ).all()
    assert real, "the real pipeline persisted a FY2026 revenue fact"
    assert all(f.status == FactStatus.UNVERIFIED.value for f in real)

    resolver = AuthoritativeMetricResolver(db_session)
    resolution = resolver.resolve(
        [f.fact_id for f in real], case_id=CASE)
    # Every real unverified fact is refused.
    assert resolution.usable_ids == set()
    assert all(
        any("not verified" in m for m in r.reasons)
        for r in resolution.rejected()
    )


def test_second_identical_real_run_reproduces_the_corpus(db_session, tmp_path):
    """Determinism: a second independent run of the same real fixture reproduces
    the identical narrative passage ids -- retrieval provenance is stable."""
    _, result = _run_real_pipeline(db_session, tmp_path / "first")
    primary_id = result.evidence_snapshot.sec_filings[0]["primary_document_id"]
    narrative = [p for p in result.evidence_snapshot.narrative_evidence
                 if p["document_id"] == primary_id]
    corpus_a = build_corpus_from_sec_narrative(narrative, document_id=primary_id)

    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    try:
        with factory() as other:
            _, result2 = _run_real_pipeline(other, tmp_path / "second")
            narrative2 = [p for p in result2.evidence_snapshot.narrative_evidence
                          if p["document_id"] == primary_id]
            corpus_b = build_corpus_from_sec_narrative(
                narrative2, document_id=primary_id)
    finally:
        engine.dispose()

    assert [p.passage_id for p in corpus_a.passages] == [
        p.passage_id for p in corpus_b.passages
    ]
