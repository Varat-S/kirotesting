"""Orchestrate existing services; canonical snapshots are the stage boundary.

The runner never approves its own work. ``run_case`` produces a draft;
``finalize_case`` consumes a separately persisted human approval.
"""

from collections import defaultdict
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import re
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.bootstrap import bootstrap_config
from app.core.config_registry import ARTIFACT_KINDS, ConfigRegistry
from app.core.hashing import canonical_json, content_hash
from app.models.orm import (
    Case,
    Document,
    Fact,
    FactSourceRef,
    ModelRun,
    MetricDefinition,
    CaseEntity,
    Snapshot,
)
from app.prompts.registry import PromptRegistry
from app.schemas.enums import FactStatus
from app.schemas.llm import AnalysisResponse, ChallengeResponse, ExtractionResponse
from app.schemas.snapshots import FinalCaseSnapshot
from app.services.analysis.challenge import ChallengeService, ChallengeRejectedError
from app.services.analysis.grounding import (
    EvidenceIndex,
    EvidenceItem,
    GroundingEvaluator,
)
from app.services.analysis.service import (
    AnalysisInputs,
    AnalysisService,
    AnalysisRejectedError,
)
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.benchmarking.peers import PeerBenchmarker, PeerValue
from app.services.entity.registry import EntityRegistry
from app.services.entity.resolution import EntityResolver, ResolutionStatus
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import (
    EscalationCategory,
    PolicyRuleEvaluator,
    RuleRegistry,
)
from app.services.extraction.base import ParserError
from app.services.extraction.pdf_table import PdfTableParser
from app.services.extraction.pdf_text import PdfTextParser
from app.services.extraction.qualitative import QualitativeExtractor
from app.services.extraction.xlsx_csv import CsvParser, XlsxParser
from app.services.extraction.xbrl import XbrlParser
from app.services.extraction.sec.statements import parse_bundle
from app.services.ingestion.sec import admit_sec_bundle
from app.services.ingestion.completeness import CompletenessEvaluator
from app.services.ingestion.service import IngestionService, TemporalLeakageError
from app.services.ingestion.storage import RawFileStore
from app.services.llm.client import (
    FakeLLMBackend,
    LLMBackend,
    LLMClient,
)
from app.services.mapping.mapper import FinancialMapper
from app.services.metrics.definitions import MetricDefinitionRegistry
from app.services.metrics.engine import MetricEngine, MetricInput
from app.services.metrics.trends import TrendAnalyzer, TrendPoint
from app.services.pipeline.package import SourcePackage, SourceInput
from app.services.pipeline.result import PipelineResult
from app.services.reconciliation.service import Reconciler, ToleranceConfig
from app.services.reconciliation.snapshot import SnapshotAssembler
from app.services.reporting.memo import MemoReportGenerator
from app.services.review.finalization import FinalSnapshotAssembler


def offline_backend() -> FakeLLMBackend:
    """Explicit empty fake responses: execute the AI stages without inventing prose."""
    backend = FakeLLMBackend()
    backend.register("extract", ExtractionResponse().model_dump(mode="json"))
    backend.register("analyze", AnalysisResponse().model_dump(mode="json"))
    backend.register("challenge", ChallengeResponse().model_dump(mode="json"))
    return backend


class CreditMemoPipeline:
    def __init__(
        self,
        session: Session,
        *,
        data_root="data",
        output_root="output",
        backend: LLMBackend | None = None,
        prompt_versions: dict[str, int] | None = None,
    ):
        self.session = session
        self.data_root = Path(data_root)
        self.output_root = Path(output_root)
        self.backend = backend
        self.prompt_versions = prompt_versions

    def run_case(
        self,
        case_id: str,
        *,
        package: SourcePackage,
        mode: str = "offline_test",
        config_versions: dict[str, int] | None = None,
    ) -> PipelineResult:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", case_id):
            raise ValueError(
                "Case ID must contain only letters, digits, underscores or hyphens."
            )
        if mode != "offline_test":
            raise ValueError("Only offline_test mode is currently supported.")
        # A failed stage rolls back its database writes; immutable source bytes
        # may remain in the content-addressed store for a retry.
        with self.session.begin_nested():
            return self._run(case_id, package, config_versions)

    def _run(self, case_id, package, pinned):
        audit = AuditLog(self.session)
        ingestion = IngestionService(
            self.session, RawFileStore(self.data_root / "raw"), audit
        )
        case = self.session.get(Case, case_id)
        if case is None:
            case = ingestion.create_case(
                case_id,
                as_of_date=package.as_of_date,
                evidence_cutoff_timestamp=package.evidence_cutoff_timestamp,
            )
        elif (case.as_of_date, case.evidence_cutoff_timestamp) != (
            package.as_of_date,
            package.evidence_cutoff_timestamp,
        ):
            raise ValueError(
                "An existing case cannot change its historical dates; create another case."
            )
        registry = ConfigRegistry(
            self.session,
            audit_hook=lambda event, kind, version, before, after: audit.record(
                event,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                before=before,
                after={**after, "artifact_kind": kind},
                linked_objects=[f"config:{kind}:{version}"],
            ),
        )
        versions = dict(pinned) if pinned is not None else bootstrap_config(registry)
        if set(versions) != set(ARTIFACT_KINDS):
            raise ValueError("A pinned run must specify all configuration versions.")
        configs = {}
        for kind, version in versions.items():
            row = registry.get(kind, version)
            if row is None:
                raise ValueError(
                    f"Pinned configuration {kind}@{version} is unavailable."
                )
            configs[kind] = row.content
        entities = EntityRegistry(self.session, audit)
        for entity in sorted(
            package.entities,
            key=lambda e: (e.parent_entity_id is not None, e.entity_id),
        ):
            entities.register(entity, case_id)
        resolver = EntityResolver(self.session, audit)
        mapper = FinancialMapper.from_registry(registry, versions["financial_mappings"])
        escalation = EscalationEngine(
            self.session, audit=audit, case_id=case_id, deterministic_ids=True
        )
        rules = RuleRegistry(self.session)
        conditions = configs["escalation_rules"]["conditions"]
        condition_versions = {}
        for condition_name, condition in conditions.items():
            condition_versions[condition_name] = self._register_rule(
                rules,
                condition["rule_id"],
                concept="data_integrity",
                category="data_integrity",
                definition=condition,
                config_kind="escalation_rules",
                config_version=versions["escalation_rules"],
            )

        def flag(condition, reason, refs=()):
            cfg = conditions[condition]
            row = condition_versions[condition]
            return escalation.raise_escalation(
                rule_id=cfg["rule_id"],
                rule_version=row.version,
                category=EscalationCategory.DATA_INTEGRITY
                if condition not in {"weak_evidence", "grounding"}
                else EscalationCategory.EVIDENCE,
                severity=cfg["severity"],
                reason=reason,
                evidence_refs=sorted(refs),
            )

        documents, facts, rejected, issues, admitted_tags = [], [], [], [], set()
        sec_filings, narrative = [], []
        sec_mapping_groups = {}
        parsers = {
            "csv": CsvParser,
            "xlsx": XlsxParser,
            "xbrl": XbrlParser,
            "pdf_table": PdfTableParser,
            "pdf_text": PdfTextParser,
        }

        def consume_source(source, parsed, did):
            # Only successfully parsed sources count towards coverage.
            admitted_tags.update(source.tags)
            audit.record(
                EventType.FACT_EXTRACTED,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                after={
                    **parsed.metadata.as_dict(),
                    "fact_count": parsed.fact_count,
                    "warnings": parsed.warnings,
                },
                linked_objects=[did],
            )
            for original in parsed.facts:
                financial = (
                    source.parser != "inline_xbrl"
                    or original.normalized_unit is not None
                )
                entity_id = original.entity_id
                if entity_id is not None and entities.get(entity_id) is None:
                    resolved = resolver.resolve(entity_id, case_id=case_id)
                    entity_id = (
                        resolved.entity_id
                        if resolved.status == ResolutionStatus.RESOLVED
                        else entity_id
                    )
                tagged = original.model_copy(
                    update={
                        "entity_id": entity_id or source.entity_id,
                        "consolidation_scope": original.consolidation_scope
                        or source.scope,
                        "accounting_basis": original.accounting_basis
                        or source.accounting_basis,
                    }
                )
                if (
                    tagged.period_type == "duration"
                    and tagged.period_start is not None
                    and tagged.period_end is not None
                    and tagged.period_start.isoformat() == f"{tagged.fiscal_year}-01-01"
                    and tagged.period_end.isoformat() == f"{tagged.fiscal_year}-12-31"
                ):
                    tagged.period_type = "FY"
                mismatch = resolver.detect_mismatch(
                    expected_entity_id=source.entity_id,
                    observed_entity_id=tagged.entity_id,
                    expected_scope=self.session.get(
                        CaseEntity, (case_id, source.entity_id)
                    ).expected_consolidation_scope
                    or source.scope,
                    observed_scope=tagged.consolidation_scope,
                    case_id=case_id,
                    fact_id=original.fact_id,
                )
                if (
                    mismatch is not None
                    or (
                        financial
                        and (tagged.fiscal_year is None or tagged.period_end is None)
                    )
                    or (
                        source.parser != "inline_xbrl"
                        and tagged.period_end is not None
                        and tagged.period_end > package.as_of_date
                    )
                ):
                    tagged.status = FactStatus.CONFLICTING
                    flag(
                        "invalid_metadata",
                        f"Unusable entity/period/scope metadata for {source.filename}:{original.name}",
                        [did],
                    )
                if not financial:
                    tagged.mapping_status = "not_financial"
                    tagged.fact_id = content_hash(
                        tagged.model_dump(mode="json", exclude={"fact_id"})
                    )
                    self._persist_fact(tagged, case_id)
                    facts.append(tagged)
                    continue
                mapped = mapper.map_fact(
                    tagged,
                    "xbrl"
                    if source.parser in {"xbrl", "inline_xbrl"}
                    else "table_label",
                )
                fact = mapped.fact
                stable = fact.model_dump(mode="json", exclude={"fact_id"})
                fact.fact_id = content_hash(stable)
                if mapped.status != "mapped":
                    if source.parser == "inline_xbrl":
                        key = (
                            fact.taxonomy_concept or original.name,
                            fact.entity_id,
                            fact.consolidation_scope,
                            fact.accounting_basis,
                            mapped.status,
                            mapped.candidates,
                        )
                        group = sec_mapping_groups.setdefault(key, {
                            "source_name": key[0],
                            "entity_id": fact.entity_id,
                            "consolidation_scope": fact.consolidation_scope,
                            "accounting_basis": fact.accounting_basis,
                            "dimensions": set(),
                            "status": mapped.status,
                            "candidates": list(mapped.candidates),
                            "mapping_version": mapper.version,
                            "fact_ids": set(),
                            "periods": set(),
                        })
                        group["fact_ids"].add(fact.fact_id)
                        group["dimensions"].add(tuple(sorted(fact.dimensions.items())))
                        group["periods"].add((
                            fact.fiscal_year, fact.period_type,
                            str(fact.period_start), str(fact.period_end),
                        ))
                        self._persist_fact(fact, case_id)
                        facts.append(fact)
                        continue
                    issue = {
                        "fact_id": fact.fact_id,
                        "source_name": original.name,
                        "status": mapped.status,
                        "candidates": list(mapped.candidates),
                        "mapping_version": mapper.version,
                    }
                    issues.append(issue)
                    audit.record(
                        EventType.MAPPING_REVIEW_REQUIRED,
                        case_id=case_id,
                        actor_type=ActorType.SYSTEM,
                        after=issue,
                        linked_objects=[fact.fact_id, did],
                    )
                    flag(
                        "mapping",
                        f"{mapped.status} financial label {original.name!r}.",
                        [fact.fact_id],
                    )
                self._persist_fact(fact, case_id)
                facts.append(fact)

        for source in sorted(
            package.sources, key=lambda s: (s.filename, s.available_at, s.entity_id)
        ):
            data = (
                source.data
                if source.data is not None
                else Path(source.path).read_bytes()
            )
            descriptor = source.model_dump(mode="json", exclude={"path", "data"})
            digest = sha256(data).hexdigest()
            did = content_hash(
                {"case": case_id, "source": descriptor, "sha256": digest}
            )
            try:
                existing = self.session.get(Document, did)
                if existing is None:
                    ingestion.ingest_document(
                        case_id,
                        data=data,
                        filename=source.filename,
                        entity_id=source.entity_id,
                        document_type=source.parser,
                        source=canonical_json(descriptor),
                        available_at=source.available_at,
                        supersedes=source.supersedes,
                        document_id=did,
                    )
            except TemporalLeakageError as exc:
                rejection = {
                    "filename": source.filename,
                    "sha256": digest,
                    "reason": exc.result.reason.value,
                    "available_at": source.available_at.isoformat(),
                }
                rejected.append(rejection)
                audit.record(
                    EventType.EVIDENCE_REJECTED,
                    case_id=case_id,
                    actor_type=ActorType.SYSTEM,
                    after=rejection,
                    reason="Source rejected by historical cutoff.",
                    linked_objects=[digest],
                )
                continue
            documents.append(
                {
                    "document_id": did,
                    "filename": source.filename,
                    "sha256": digest,
                    "available_at": source.available_at.isoformat(),
                    "parser": source.parser,
                    "tags": sorted(source.tags),
                    "entity_id": source.entity_id,
                    "extraction_recipe": descriptor,
                }
            )
            options = dict(source.parse_options)
            if source.parser == "pdf_text":
                options.update(
                    field_patterns=source.field_patterns,
                    numeric_fields=set(source.numeric_fields),
                )
            try:
                if source.parser not in parsers:
                    raise ParserError(
                        "Inline-XBRL requires a declared SEC filing bundle."
                    )
                parsed = parsers[source.parser]().parse(
                    data, document_id=did, **options
                )
            except ParserError as exc:
                flag(
                    "invalid_metadata",
                    f"Parser failed for {source.filename}: {exc}",
                    [did],
                )
                continue
            consume_source(source, parsed, did)

        for entry in package.sec_bundles:
            admitted, sec_documents, excluded = admit_sec_bundle(
                entry.bundle,
                entity_id=entry.entity_id,
                case_id=case_id,
                ingestion=ingestion,
                session=self.session,
                audit=audit,
            )
            documents.extend(sec_documents)
            rejected.extend(excluded)
            primary = next(
                (f for f in admitted.files if f.role == "primary_inline_xbrl"), None
            )
            if primary is None:
                continue
            try:
                parsed, diagnostics, passages = parse_bundle(
                    admitted,
                    entity_id=entry.entity_id,
                    scope=entry.scope,
                    accounting_basis=entry.accounting_basis,
                )
            except ParserError as exc:
                flag(
                    "invalid_metadata",
                    f"SEC parser failed for {primary.filename}: {exc}",
                    [primary.document_id],
                )
                continue
            sec_filings.append(diagnostics)
            narrative.extend(passages)
            audit.record(
                EventType.INLINE_XBRL_PARSED,
                case_id=case_id,
                after={
                    "accession": admitted.accession,
                    "fact_count": parsed.fact_count,
                    "parser_version": diagnostics["parser_version"],
                },
                linked_objects=[primary.document_id],
            )
            audit.record(
                EventType.NARRATIVE_EVIDENCE_EXTRACTED,
                case_id=case_id,
                after={"passage_count": len(passages)},
                linked_objects=sorted({p["document_id"] for p in passages}),
            )
            for companion in admitted.files:
                if companion.role == "proxy":
                    audit.record(
                        EventType.PROXY_LINKED,
                        case_id=case_id,
                        after={"accession": companion.accession},
                        linked_objects=[companion.document_id, primary.document_id],
                    )
                if companion.role == "exhibit_21":
                    audit.record(
                        EventType.SUBSIDIARIES_EXTRACTED,
                        case_id=case_id,
                        after={"count": len(diagnostics["subsidiaries"])},
                        linked_objects=[companion.document_id],
                    )
            consume_source(
                SourceInput(
                    filename=primary.filename,
                    data=primary.content,
                    parser="inline_xbrl",
                    entity_id=entry.entity_id,
                    scope=entry.scope,
                    accounting_basis=entry.accounting_basis,
                    tags=["sec", "financial_statements"],
                    available_at=primary.available_at,
                ),
                parsed,
                primary.document_id,
            )

        for group in sec_mapping_groups.values():
            issue = {
                **group,
                "fact_ids": sorted(group["fact_ids"]),
                "observation_count": len(group["fact_ids"]),
                "dimensions": [dict(d) for d in sorted(group["dimensions"])],
                "periods": [list(p) for p in sorted(group["periods"], key=str)],
            }
            issues.append(issue)
            audit.record(
                EventType.MAPPING_REVIEW_REQUIRED,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                after=issue,
                linked_objects=issue["fact_ids"],
            )
            flag(
                "mapping",
                f"{issue['status']} financial concept {issue['source_name']!r} "
                f"for {issue['entity_id']} ({issue['consolidation_scope']}, "
                f"{issue['accounting_basis']}): "
                f"{issue['observation_count']} observations.",
                issue["fact_ids"],
            )
        documents = list({d["document_id"]: d for d in documents}.values())
        sec_filings = list({content_hash(d): d for d in sec_filings}.values())
        narrative = list({p["evidence_id"]: p for p in narrative}.values())
        facts = sorted({f.fact_id: f for f in facts}.values(), key=lambda f: f.fact_id)
        completeness = CompletenessEvaluator(registry).assess(
            admitted_tags, profile_version=versions["source_profiles"]
        )
        for tag in completeness.missing_critical:
            flag("missing_critical", f"Missing critical evidence: {tag}.")
        for tag in completeness.missing_important:
            flag("missing_important", f"Reduced coverage: missing {tag}.")
        groups = defaultdict(list)
        for fact in facts:
            if fact.mapping_status == "mapped":
                groups[
                    (
                        fact.entity_id,
                        fact.fiscal_year,
                        fact.period_type,
                        str(fact.period_end),
                        fact.name,
                        tuple(sorted(fact.dimensions.items())),
                        str(fact.period_start),
                    )
                ].append(fact)
        reconciler = Reconciler(
            ToleranceConfig(configs["tolerances"], versions["tolerances"]),
            session=self.session,
            audit=audit,
            case_id=case_id,
            precedence=configs["parser_precedence"],
            selection_version=versions["parser_precedence"],
        )
        reconciliations = []
        for key, observations in sorted(groups.items(), key=lambda item: str(item[0])):
            result = reconciler.reconcile_field(key[4], observations)
            result.field = canonical_json(key)
            reconciler.persist(result)
            reconciliations.append(result)
            if result.resolved_state == FactStatus.CONFLICTING:
                flag(
                    "conflict",
                    f"Conflicting canonical field {result.field}.",
                    result.fact_ids,
                )

        assembler = SnapshotAssembler(self.session, registry)
        evidence = assembler.assemble(
            case_id=case_id,
            facts=facts,
            reconciliations=reconciliations,
            documents=sorted(documents, key=lambda d: d["document_id"]),
            entities=sorted(package.entities, key=lambda e: e.entity_id),
            as_of_date=package.as_of_date,
            evidence_cutoff_timestamp=package.evidence_cutoff_timestamp,
        )
        # Exact pinned versions, rather than whatever became latest subsequently.
        evidence.config_versions = versions
        evidence.sec_filings = sec_filings
        evidence.narrative_evidence = sorted(narrative, key=lambda p: p["evidence_id"])
        evidence.financials = {
            key: {
                "value": dq.value,
                "state": dq.state,
                "selected_fact_id": dq.selected_fact_id,
            }
            for key, dq in evidence.data_quality.items()
        }
        evidence_row = assembler.persist(evidence)
        metrics, all_metrics, definition_versions = self._metrics(
            case_id, evidence, configs, audit, package.borrower_entity_id
        )
        required_missing = [
            name for name, metric in metrics.items() if metric.state.value != "ok"
        ]
        if required_missing:
            flag(
                "missing_critical",
                "Unavailable deterministic metrics: "
                + ", ".join(sorted(required_missing)),
            )
        weak = [
            name
            for name, metric in metrics.items()
            if metric.review_required and metric.result is not None
        ]
        if weak:
            flag(
                "weak_evidence",
                "Metrics require evidence review: " + ", ".join(sorted(weak)),
            )
        trends, benchmarks, outcomes = self._context(
            case_id, package, metrics, all_metrics, configs, versions, rules, audit
        )
        for outcome in outcomes:
            escalation.raise_from_outcome(outcome)
        analysis, runs, grounding, qualitative = self._ai(
            case_id,
            evidence,
            metrics,
            trends,
            benchmarks,
            completeness,
            configs,
            escalation,
            audit,
            flag,
        )
        final = FinalSnapshotAssembler(self.session, registry, audit=audit)
        # Timestamp resolution can change ordering across otherwise equal runs.
        # Canonical memo content uses stable IDs instead of wall-clock order.
        esc_payloads = sorted(
            [self._escalation_payload(e) for e in escalation.escalations_for_case()],
            key=lambda e: e["escalation_id"],
        )
        draft = final.assemble(
            case_id=case_id,
            evidence_snapshot_version=evidence.snapshot_version,
            metrics={k: v.as_payload() for k, v in metrics.items()},
            benchmarks=benchmarks,
            business_analysis=analysis,
            financial_analysis={
                "trends": trends,
                "grounding": grounding,
                "qualitative_facts": qualitative,
                "data_limitations": asdict(completeness),
            },
            risks=analysis.get("key_risks", []),
            mitigants=analysis.get("mitigants", []),
            escalations=esc_payloads,
            metric_definition_versions=definition_versions,
            rule_versions={
                **{row.rule_id: row.version for row in condition_versions.values()},
                **{outcome.rule_id: outcome.rule_version for outcome in outcomes},
            },
            prompt_model_versions=self._prompt_versions(runs),
            recommendation={
                "status": "draft",
                "human_sign_off_required": True,
                "inability_to_conclude": bool(
                    required_missing or completeness.missing_critical
                ),
            },
        )
        draft.config_versions = versions
        draft_row = final.persist_draft(draft)
        reporter = MemoReportGenerator(
            self.session, audit=audit, output_root=self.output_root
        )
        memo = reporter.generate_draft_json(
            case_id=case_id, snapshot_version=draft.snapshot_version
        )
        paths = self._write_draft(case_id, draft.snapshot_version, memo, reporter)
        audit.record(
            EventType.DRAFT_MEMO_GENERATED,
            case_id=case_id,
            actor_type=ActorType.SYSTEM,
            after={
                "snapshot_version": draft.snapshot_version,
                "memo_hash": content_hash(memo),
            },
            reason="Draft outputs generated; human approval remains outstanding.",
        )
        return PipelineResult(
            evidence,
            evidence_row,
            draft_row,
            metrics,
            benchmarks,
            trends,
            esc_payloads,
            sorted(d["sha256"] for d in documents),
            rejected,
            runs,
            issues,
            memo,
            paths,
            evaluation_artifacts={
                "missing_metrics": required_missing,
                "coverage": asdict(completeness),
                "unsupported_claims": sum(not g["is_grounded"] for g in grounding),
            },
        )

    def _persist_fact(self, fact, case_id):
        if self.session.get(Fact, fact.fact_id) is not None:
            return
        payload = fact.model_dump(exclude={"source_refs"})
        payload["status"] = fact.status.value
        payload["extraction_method"] = (
            fact.extraction_method.value if fact.extraction_method else None
        )
        row = Fact(case_id=case_id, **payload)
        row.source_refs = [
            FactSourceRef(**ref.model_dump()) for ref in fact.source_refs
        ]
        self.session.add(row)
        self.session.flush()

    def _metrics(self, case_id, evidence, configs, audit, borrower):
        registry = MetricDefinitionRegistry(
            self.session,
            audit_hook=lambda name, version, before, after: audit.record(
                EventType.METRIC_DEFINITION_CHANGED,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                before=before,
                after={**after, "metric": name},
                linked_objects=[f"metric_def:{name}:{version}"],
            ),
        )
        definitions = {}
        for name, spec in sorted(configs["metric_defs"]["definitions"].items()):
            matching = self.session.scalars(
                select(MetricDefinition)
                .where(
                    MetricDefinition.metric_definition_id == name,
                    MetricDefinition.content_hash == content_hash(spec),
                )
                .order_by(MetricDefinition.version)
            ).first()
            definitions[name] = (
                registry.get(name, matching.version)
                if matching
                else registry.register(name, spec)
            )
        # Only FY duration/instant observations sharing the exact year-end are
        # eligible. Quarterly/YTD observations stay in the evidence snapshot.
        pools = defaultdict(lambda: defaultdict(list))
        facts_by_id = {f["fact_id"]: f for f in evidence.facts}
        for key, dq in evidence.data_quality.items():
            entity, year, period_type, end, name, *identity = json.loads(key)
            dimensional = identity and bool(identity[0])
            selected = facts_by_id.get(dq.selected_fact_id, {})
            consolidated = selected.get("consolidation_scope") in {None, "consolidated"}
            if (
                year is not None
                and period_type in {"FY", "instant"}
                and not dimensional
                and consolidated
                and end <= evidence.as_of_date.isoformat()
            ):
                pools[(entity, int(year), end)][name].append(dq)
        all_metrics = defaultdict(dict)
        for (entity, year, end), fields in sorted(pools.items()):
            inputs = {}
            for name, states in fields.items():
                if len(states) != 1:
                    inputs[name] = MetricInput(name, None, FactStatus.CONFLICTING)
                else:
                    dq = states[0]
                    inputs[name] = MetricInput(
                        name, dq.value, FactStatus(dq.state), dq.selected_fact_id
                    )
            prior_periods = [
                k
                for k in pools
                if k[0] == entity and k[1] == year - 1 and "revenue" in pools[k]
            ]
            previous = (
                pools[max(prior_periods, key=lambda k: k[2])].get("revenue", [])
                if prior_periods
                else []
            )
            if len(previous) == 1:
                dq = previous[0]
                inputs["revenue_prior"] = MetricInput(
                    "revenue_prior", dq.value, FactStatus(dq.state), dq.selected_fact_id
                )
            engine = MetricEngine(
                near_zero_floor=configs["tolerances"]["near_zero_floor"],
                session=self.session,
                audit=audit,
                case_id=case_id,
            )
            for name, definition in definitions.items():
                required_inputs = {
                    k: inputs[k] for k in definition.inputs if k in inputs
                }
                metric = engine.compute(
                    definition, required_inputs, period=end, fiscal_year=year
                )
                engine.persist(metric)
                all_metrics[(entity, year, end)][name] = metric
        targets = [
            k
            for k in all_metrics
            if k[0] == borrower and k[2] == evidence.as_of_date.isoformat()
        ]
        target = (
            targets[0]
            if len(targets) == 1
            else (borrower, evidence.as_of_date.year, evidence.as_of_date.isoformat())
        )
        current = all_metrics.get(target)
        if current is None:
            engine = MetricEngine(session=self.session, audit=audit, case_id=case_id)
            current = {}
            for name, definition in definitions.items():
                current[name] = engine.compute(
                    definition,
                    {},
                    period=evidence.as_of_date.isoformat(),
                    fiscal_year=evidence.as_of_date.year,
                )
                engine.persist(current[name])
        return (
            current,
            all_metrics,
            {name: d.version for name, d in definitions.items()},
        )

    def _context(
        self, case_id, package, metrics, all_metrics, configs, versions, rules, audit
    ):
        analyzer = TrendAnalyzer(
            configs["trend_rules"]["adverse_directions"],
            break_threshold=configs["trend_rules"]["break_threshold"],
        )
        benchmarker = PeerBenchmarker(
            min_sample_for_percentiles=configs["peers"]["min_sample_for_percentiles"],
            session=self.session,
            audit=audit,
            case_id=case_id,
        )
        trends, benchmarks, outcomes = {}, {}, []
        evaluator = PolicyRuleEvaluator(
            configs["policy"], policy_version=versions["policy"]
        )
        for name, metric in sorted(metrics.items()):
            points = [
                TrendPoint(year, values[name].result)
                for (entity, year, end), values in sorted(all_metrics.items())
                if entity == package.borrower_entity_id
            ]
            trends[name] = analyzer.analyze(name, points).as_payload()
            peer_values = []
            for entity in sorted(configs["peers"]["entities"]):
                peer = all_metrics.get(
                    (entity, package.as_of_date.year, package.as_of_date.isoformat()),
                    {},
                ).get(name)
                if peer is not None and peer.result is not None:
                    peer_values.append(
                        PeerValue(
                            entity, peer.result, synthetic=configs["peers"]["synthetic"]
                        )
                    )
            bench = benchmarker.benchmark(
                name,
                package.borrower_entity_id,
                metric.result,
                peer_values,
                cohort_definition=configs["peers"],
                cohort_version=versions["peers"],
                benchmark_date=package.as_of_date,
            )
            benchmarker.persist(bench)
            benchmarks[name] = bench.as_payload()
            if name not in configs["policy"]["metrics"]:
                continue
            for concept, method in [
                ("policy_threshold", evaluator.evaluate_policy_threshold),
                ("peer_benchmark", evaluator.evaluate_peer_benchmark),
                (
                    "historical_deterioration",
                    evaluator.evaluate_historical_deterioration,
                ),
            ]:
                rule_id = configs["policy"]["metrics"][name]["rule_ids"][concept]
                version = self._register_rule(
                    rules,
                    rule_id,
                    concept=concept,
                    category="financial_rules",
                    definition=configs["policy"]["metrics"][name],
                    config_kind="policy",
                    config_version=versions["policy"],
                ).version
                kw = {
                    "rule_id": rule_id,
                    "rule_version": version,
                    "evidence_refs": metric.input_fact_ids,
                }
                if concept == "policy_threshold":
                    outcome = method(name, metric.result, **kw)
                elif concept == "peer_benchmark":
                    outcome = method(name, metric.result, bench.p90, **kw)
                else:
                    trend = trends[name]
                    outcome = method(
                        name,
                        trend["pct_change_2y"],
                        direction_is_adverse=trend["direction"] == "deteriorating",
                        **kw,
                    )
                outcomes.append(outcome)
        return trends, benchmarks, outcomes

    def _ai(
        self,
        case_id,
        evidence,
        metrics,
        trends,
        benchmarks,
        completeness,
        configs,
        escalation,
        audit,
        flag,
    ):
        prompts = PromptRegistry(self.session)
        prompts.register_catalogue()
        client = LLMClient(
            self.backend or offline_backend(),
            prompts=prompts,
            session=self.session,
            audit=audit,
            prompt_versions=self.prompt_versions,
        )
        qualitative, extraction = QualitativeExtractor(
            client, audit=audit, case_id=case_id
        ).extract(
            {"canonical_evidence": evidence.model_dump(mode="json")},
            evidence_ids=[d["document_id"] for d in evidence.documents],
            case_version=evidence.snapshot_version,
        )
        if not extraction.is_valid:
            return self._rejected_ai(
                case_id,
                evidence.snapshot_version,
                flag,
                "Qualitative extraction was rejected; human review required.",
            )
        known_docs = {d["document_id"] for d in evidence.documents}
        for fact in qualitative:
            if any(ref.document_id not in known_docs for ref in fact.source_refs):
                return self._rejected_ai(
                    case_id,
                    evidence.snapshot_version,
                    flag,
                    "AI extraction cited evidence outside the admitted source set.",
                )
            if fact.normalized_value is not None:
                # An extraction output is never a route around deterministic
                # mapping/reconciliation for numerical values.
                flag(
                    "grounding",
                    f"AI numeric extraction requires review: {fact.name}.",
                    [fact.fact_id],
                )
            self._persist_fact(fact, case_id)
        limitations = [
            f"Missing {tag}"
            for tag in completeness.missing_critical + completeness.missing_important
        ]
        inputs = AnalysisInputs(
            canonical_facts=evidence.facts
            + [f.model_dump(mode="json") for f in qualitative],
            snippets=evidence.narrative_evidence,
            metrics=[m.as_payload() for m in metrics.values()],
            trends=list(trends.values()),
            benchmarks=list(benchmarks.values()),
            data_limitations=limitations,
            open_conflicts=[
                dq.model_dump(mode="json")
                for dq in evidence.data_quality.values()
                if dq.state == "conflicting"
            ],
            missing_critical_evidence=completeness.missing_critical,
        )
        try:
            analysis = AnalysisService(
                client, escalation_engine=escalation, case_id=case_id
            ).analyze(inputs, case_version=evidence.snapshot_version)
            challenge = ChallengeService(
                client, escalation_engine=escalation, audit=audit, case_id=case_id
            ).challenge(
                analysis.analysis,
                inputs.to_payload(),
                case_version=evidence.snapshot_version,
            )
        except (AnalysisRejectedError, ChallengeRejectedError) as exc:
            return self._rejected_ai(case_id, evidence.snapshot_version, flag, str(exc))
        # Ground only selected canonical facts and metrics; raw conflicting
        # observations must never substantiate a narrative financial claim.
        fact_index = {fact["fact_id"]: fact for fact in evidence.facts}
        items = [
            EvidenceItem(
                dq.selected_fact_id,
                value=dq.value,
                unit=fact_index[dq.selected_fact_id].get("normalized_unit"),
            )
            for dq in evidence.data_quality.values()
            if dq.selected_fact_id and dq.state == "verified"
        ]
        items += [
            EvidenceItem(name, value=m.result, unit=m.units)
            for name, m in metrics.items()
            if m.result is not None and not m.review_required
        ]
        # Candidate passages establish citation presence; they do not establish
        # entailment or turn heuristic topic tags into verified conclusions.
        items += [
            EvidenceItem(p["evidence_id"], text=p["text"])
            for p in evidence.narrative_evidence
        ]
        evaluator = GroundingEvaluator(
            EvidenceIndex(items), session=self.session, case_id=case_id
        )
        grounding = []
        for claim in analysis.analysis.all_claims():
            result = evaluator.evaluate_claim(claim)
            evaluator.persist(result)
            payload = result.model_dump(mode="json")
            payload["is_grounded"] = result.is_grounded
            grounding.append(payload)
            if not result.is_grounded:
                flag(
                    "grounding",
                    f"Claim {claim.claim_id} requires grounding review.",
                    claim.evidence_ids,
                )
        runs = self._model_runs(case_id, evidence.snapshot_version)
        output = analysis.analysis.model_dump(mode="json")
        output["challenges"] = [c.model_dump(mode="json") for c in challenge.challenges]
        return output, runs, grounding, [f.model_dump(mode="json") for f in qualitative]

    def _model_runs(self, case_id, version):
        stored = self.session.scalars(
            select(ModelRun)
            .where(
                ModelRun.case_id == case_id,
                ModelRun.case_version == version,
            )
            .order_by(ModelRun.method)
        ).all()
        return [
            {
                "run_id": run.run_id,
                "method": run.method,
                "model_id": run.model_id,
                "prompt_id": run.prompt_id,
                "prompt_version": run.prompt_version,
                "prompt_hash": run.prompt_hash,
                "raw_response": run.raw_response,
                "parsed": run.parsed_response,
                "validation_outcome": run.validation_outcome,
            }
            for run in stored
        ]

    def _rejected_ai(self, case_id, version, flag, reason):
        # Preserve the rejected ModelRun and audit log, and retain deterministic
        # work in a blocked draft. No invalid narrative enters canonical output.
        flag("grounding", reason)
        response = AnalysisResponse(
            data_limitations=[reason],
            questions_for_human=["Review rejected AI output before finalization."],
        )
        return (
            response.model_dump(mode="json"),
            self._model_runs(case_id, version),
            [],
            [],
        )

    @staticmethod
    def _register_rule(registry, rule_id, **kwargs):
        matching = registry.version_for_definition(rule_id, kwargs["definition"])
        return matching or registry.register(rule_id, **kwargs)

    @staticmethod
    def _prompt_versions(runs):
        return {
            run["method"]: f"{run['prompt_id']}:{run['prompt_hash']}:{run['model_id']}"
            for run in runs
        }

    @staticmethod
    def _escalation_payload(view):
        return {
            "escalation_id": view.escalation_id,
            "rule_id": view.rule_id,
            "severity": view.severity,
            "category": view.category,
            "mandatory": view.mandatory,
            "reason": view.reason,
            "status": view.status,
            "evidence_refs": sorted(view.evidence_refs),
        }

    def _write_draft(self, case_id, version, memo, reporter):
        folder = self.output_root / case_id
        folder.mkdir(parents=True, exist_ok=True)
        paths = {
            "json": folder / f"draft_v{version}.json",
            "html": folder / f"draft_v{version}.html",
        }
        paths["json"].write_text(canonical_json(memo) + "\n", encoding="utf-8")
        paths["html"].write_text(reporter.render_html(memo), encoding="utf-8")
        return paths

    def finalize_case(
        self,
        case_id: str,
        snapshot_version: int,
        *,
        signed_off_by: str,
        produce_pdf=False,
    ):
        """Consume persisted human approval; never create it on the caller's behalf."""
        row = self.session.scalars(
            select(Snapshot).where(
                Snapshot.case_id == case_id,
                Snapshot.snapshot_type == "final_case",
                Snapshot.snapshot_version == snapshot_version,
            )
        ).one()
        final = FinalSnapshotAssembler(
            self.session, ConfigRegistry(self.session), audit=AuditLog(self.session)
        )
        frozen = final.finalize(
            FinalCaseSnapshot.model_validate(row.payload), signed_off_by=signed_off_by
        )
        output = MemoReportGenerator(
            self.session, audit=AuditLog(self.session), output_root=self.output_root
        ).generate(
            case_id=case_id,
            snapshot_version=snapshot_version,
            write=True,
            produce_pdf=produce_pdf,
        )
        return frozen, output
