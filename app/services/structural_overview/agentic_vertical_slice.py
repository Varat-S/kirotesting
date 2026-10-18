"""Actual BO specialists + simulated FO + governed SO evaluation slice.

The BO source is a specialists-only run, not an accepted BO handoff.  This
module therefore produces an evaluation report rather than manufacturing a
topic conclusion, score, human approval, or final credit decision.
"""

from __future__ import annotations

import json
import textwrap
from copy import deepcopy
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from app.core.hashing import canonical_json, content_hash
from app.schemas.bo import HypothesisRecord
from app.schemas.bo_m3 import M3CorporateAssessment
from app.schemas.credit_memo import FinancialOverview
from app.services.bo.m3_validation import validate_m3_assessment
from app.services.structural_overview.credit_memo import calculate_financials


REPORT_VERSION = "bo-fo-so-agentic-vertical-slice-1.0"
DISCLOSURE = (
    "INCOMPLETE EVALUATION - actual live BO specialist outputs, simulated financial "
    "information, and offline-governed SO input; no accepted BO conclusion, real FO, "
    "final rating, facility decision, or human approval."
)


class AgenticVerticalSliceError(ValueError):
    """The recorded slice is incomplete, inconsistent, or tampered."""


def _bind_synthetic_fo(
    source: dict[str, Any], *, case_id: str, borrower_name: str,
) -> tuple[FinancialOverview, dict[str, Any]]:
    original = deepcopy(source)
    try:
        provenance = original["provenance"]
    except KeyError as exc:
        raise AgenticVerticalSliceError("FO packet has no provenance.") from exc
    if provenance != "synthetic_fixture":
        if (original.get("case_id") != case_id
                or original.get("borrower_name") != borrower_name):
            raise AgenticVerticalSliceError(
                "A non-synthetic FO packet cannot be rebound to another case."
            )
        binding = {"rebound": False}
    else:
        binding = {
            "rebound": (
                original.get("case_id") != case_id
                or original.get("borrower_name") != borrower_name
            ),
            "source_case_id": original.get("case_id"),
            "target_case_id": case_id,
            "source_borrower_name": original.get("borrower_name"),
            "target_borrower_name": borrower_name,
            "reason": (
                "The existing invented FO fixture is deterministically scoped to the "
                "recorded BO case; its figures and synthetic provenance are unchanged."
            ),
        }
        original["case_id"] = case_id
        original["borrower_name"] = borrower_name
        original["source_run_id"] = f"fo_synthetic_bound_{case_id.lower()}"
    try:
        return FinancialOverview.model_validate(original), binding
    except Exception as exc:
        raise AgenticVerticalSliceError(f"Invalid FO packet: {exc}") from exc


def _govern_m3(agent: dict[str, Any]) -> tuple[M3CorporateAssessment, dict[str, Any]]:
    calls = [
        call for call in agent.get("calls", [])
        if isinstance((call.get("output") or {}).get("m3_assessment"), dict)
    ]
    if not calls:
        raise AgenticVerticalSliceError("Recorded M3 agent has no assessment proposal.")
    call = calls[-1]
    evidence = (call.get("inputs") or {}).get("evidence") or []
    allowed = {
        item["evidence_id"] for item in evidence
        if isinstance(item, dict) and item.get("evidence_id")
    }
    texts = {
        item["evidence_id"]: item.get("text") or ""
        for item in evidence
        if isinstance(item, dict) and item.get("evidence_id")
    }
    try:
        governed = validate_m3_assessment(
            call["output"]["m3_assessment"],
            allowed_evidence_ids=allowed,
            validated_borrower_entity_ids=set(),
            evidence_texts=texts,
        )
    except Exception as exc:
        raise AgenticVerticalSliceError(f"M3 replay failed: {exc}") from exc
    replay = {
        "kind": "offline_deterministic_validator_replay",
        "model_called": False,
        "source_agent_run_id": (agent.get("agent_run_ids") or [None])[-1],
        "source_call_number": agent.get("calls", []).index(call) + 1,
        "allowed_evidence_ids": sorted(allowed),
        "transaction_borrower_entity_ids": [],
        "limitation": (
            "No transaction borrower was supplied. Reporting-company identity and "
            "historical issuance cannot establish the proposed facility obligor."
        ),
    }
    return governed, replay


def _call_summary(call: dict[str, Any], number: int) -> dict[str, Any]:
    inputs = call.get("inputs") or {}
    evidence = inputs.get("evidence") or []
    return {
        "call_number": number,
        "prompt_id": call.get("prompt_id"),
        "prompt_hash": call.get("prompt_hash"),
        "model_id": call.get("model_id"),
        "usage": call.get("usage") or {},
        "latency_ms": call.get("latency_ms"),
        "error": call.get("error"),
        "input_summary": {
            "evidence_ids": [
                item.get("evidence_id") for item in evidence
                if isinstance(item, dict) and item.get("evidence_id")
            ],
            "indicator_ids": [
                item.get("parameter_result_id") for item in inputs.get("indicators") or []
                if isinstance(item, dict)
            ],
            "check_ids": [
                item.get("check_id") for item in inputs.get("checklist") or []
                if isinstance(item, dict)
            ],
            "retrieval": inputs.get("retrieval"),
        },
        "structured_output": call.get("output"),
    }


def build_agentic_vertical_slice(
    bo_payload: dict[str, Any], fo_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a deterministic, explicitly incomplete BO/FO/SO evaluation packet."""
    if bo_payload.get("document") != "business_overview_specialists_run":
        raise AgenticVerticalSliceError("Expected a BO specialists-run artifact.")
    if bo_payload.get("version") != "bo-specialists-run-1.0":
        raise AgenticVerticalSliceError("Unsupported BO specialists-run version.")
    meta = bo_payload.get("meta") or {}
    required_meta = ("analysis_run_id", "case_id", "borrower", "as_of_date")
    if any(not meta.get(key) for key in required_meta):
        raise AgenticVerticalSliceError("BO specialists-run identity is incomplete.")
    agents = bo_payload.get("agents") or []
    if not agents or len({agent.get("agent_id") for agent in agents}) != len(agents):
        raise AgenticVerticalSliceError("BO specialist identities are missing or duplicated.")

    normalized_agents = []
    hypothesis_ids: set[str] = set()
    all_hypotheses: list[HypothesisRecord] = []
    for agent in agents:
        if agent.get("status") != "validated":
            raise AgenticVerticalSliceError(
                f"BO specialist {agent.get('agent_id')!r} is not validated."
            )
        records = []
        for raw in agent.get("hypotheses") or []:
            try:
                record = HypothesisRecord.model_validate(raw)
            except Exception as exc:
                raise AgenticVerticalSliceError(
                    f"Invalid recorded hypothesis for {agent.get('agent_id')}: {exc}"
                ) from exc
            if record.analysis_run_id != meta["analysis_run_id"]:
                raise AgenticVerticalSliceError("Cross-run BO hypothesis detected.")
            if record.hypothesis_id in hypothesis_ids:
                raise AgenticVerticalSliceError(
                    f"Duplicate BO hypothesis ID {record.hypothesis_id!r}."
                )
            hypothesis_ids.add(record.hypothesis_id)
            all_hypotheses.append(record)
            records.append(record.model_dump(mode="json"))
        normalized_agents.append({
            "agent_id": agent.get("agent_id"),
            "code": agent.get("code"),
            "title": agent.get("title"),
            "status": agent.get("status"),
            "detail": agent.get("detail"),
            "agent_run_ids": agent.get("agent_run_ids") or [],
            "owned_parameters": agent.get("owned_parameters") or [],
            "parameters": agent.get("parameters") or [],
            "parameter_adjustments": agent.get("parameter_adjustments") or [],
            "hypotheses": records,
            "coverage": agent.get("coverage") or [],
            "validation_issues": agent.get("validation_issues") or [],
            "validator_adjustments": agent.get("validator_adjustments") or [],
            "retrieval_attempts": agent.get("retrieval_attempts") or [],
            "effective_instructions": (
                (agent.get("calls") or [{}])[0].get("effective_instructions")
            ),
            "calls": [
                _call_summary(call, index)
                for index, call in enumerate(agent.get("calls") or [], 1)
            ],
        })

    m3_agent = next(
        (agent for agent in agents if agent.get("agent_id") == "m3_corporate_management"),
        None,
    )
    if m3_agent is None:
        raise AgenticVerticalSliceError("M3 specialist is absent.")
    governed_m3, replay = _govern_m3(m3_agent)
    fo, binding = _bind_synthetic_fo(
        fo_payload, case_id=meta["case_id"], borrower_name=meta["borrower"])
    if str(fo.periods[-1].fiscal_year) != str(meta["as_of_date"])[:4]:
        raise AgenticVerticalSliceError("FO latest period does not match BO as-of year.")
    calculations = calculate_financials(fo)

    usable = [
        item for item in all_hypotheses
        if item.acceptance_state.value == "accepted" and item.admissibility.value != "audit_only"
    ]
    high_material = [item for item in usable if item.materiality.value == "high"]
    cross_source = next((
        item for item in usable
        if item.section == "M1A6"
        and "margin" in item.claim.casefold()
        and ("labor" in item.claim.casefold() or "cost" in item.claim.casefold())
    ), None)
    cross_domain = []
    if cross_source is not None:
        cross_domain.append({
            "finding_id": "BO-FO-R5-001",
            "text": (
                "The actual BO specialist identified a qualified margin-pressure "
                "interaction. The simulated FO margin series illustrates how that "
                "risk could be monitored; it does not measure HCA or increase BO certainty."
            ),
            "bo_hypothesis_ids": [cross_source.hypothesis_id],
            "bo_evidence_ids": cross_source.supporting_evidence_ids,
            "financial_metric_refs": [
                f"{item.period}:{item.metric_id}" for item in calculations
                if item.metric_id == "operating_margin"
            ],
            "status": "unverified_simulation_dependent",
        })

    source_bo_hash = content_hash(bo_payload)
    source_fo_hash = content_hash(fo_payload)
    normalized_fo = fo.model_dump(mode="json")
    payload: dict[str, Any] = {
        "document": "bo_fo_so_agentic_vertical_slice",
        "version": REPORT_VERSION,
        "disclosure": DISCLOSURE,
        "case": {
            "case_id": meta["case_id"],
            "reporting_entity_name": meta["borrower"],
            "as_of_date": meta["as_of_date"],
            "evidence_cutoff": meta.get("evidence_cutoff"),
            "transaction_borrower_status": governed_m3.borrower_identification.status,
            "transaction_borrower_entity_id": (
                governed_m3.borrower_identification.entity_id
                if governed_m3.borrower_identification.status == "identified" else None
            ),
            "transaction_borrower_limitation": governed_m3.borrower_identification.limitation,
        },
        "completion": {
            "bo_specialists": "validated_live_outputs",
            "bo_topic_conclusion": "not_executed",
            "bo_human_review": "not_performed",
            "financial_overview": "simulated_fixture",
            "structural_overview": "partial_offline_governed_m3",
            "facility_underwriting": "blocked",
            "final_credit_synthesis": "blocked",
        },
        "business_overview": {
            "source_type": "live_specialists_only_no_accepted_handoff",
            "meta": meta,
            "metrics": bo_payload.get("metrics") or {},
            "usage": bo_payload.get("usage") or {},
            "stages": bo_payload.get("stages") or [],
            "agents": normalized_agents,
            "high_materiality_usable_findings": [
                item.model_dump(mode="json") for item in high_material
            ],
            "fo_handoffs": bo_payload.get("fo_handoffs") or [],
            "shared_rules": bo_payload.get("shared_rules"),
            "source_documents": meta.get("documents") or [],
            "evidence_catalog": bo_payload.get("passages") or {},
            "limitation": (
                "This run stopped after specialists and deterministic validators. "
                "No BO synthesis, challenger, score, accepted handoff, or human review exists."
            ),
        },
        "financial_overview": {
            "packet": normalized_fo,
            "calculations": [item.model_dump(mode="json") for item in calculations],
            "fixture_binding": binding,
            "limitation": (
                "Every financial value and finding is invented test data. No value "
                "was extracted from the HCA filing or admitted as real evidence."
            ),
        },
        "structural_overview": {
            "source": "final recorded M3 proposal replayed through current Validator 7",
            "replay": replay,
            "governed_assessment": governed_m3.model_dump(mode="json"),
            "limitation": (
                "Validator admission is not a legal opinion. SO must independently "
                "establish the facility obligor, ranking, restrictions, guarantees, "
                "collateral, covenants, and enforceability."
            ),
        },
        "cross_domain_findings": cross_domain,
        "required_next_steps": [
            "Run BO synthesis and challenge, then obtain explicit human acceptance.",
            "Replace the synthetic FO packet with admitted, reconciled financial analysis.",
            "Confirm the transaction borrower from transaction-specific evidence.",
            "Complete legal ranking, restrictions, guarantees, collateral, covenants, and facility terms.",
            "Run cross-topic challenge and the guarded Credit Orchestrator only after prerequisites are accepted.",
        ],
        "final_credit_score": None,
        "facility_approval": None,
        "integrity": {
            "source_bo_content_hash": source_bo_hash,
            "source_fo_fixture_content_hash": source_fo_hash,
            "normalized_fo_content_hash": content_hash(normalized_fo),
            "m3_validator_version": governed_m3.validator_version,
            "hash_scope": "All report fields except report_content_hash.",
        },
    }
    payload["report_content_hash"] = content_hash(payload)
    return validate_agentic_vertical_slice(payload)


def validate_agentic_vertical_slice(payload: dict[str, Any]) -> dict[str, Any]:
    """Revalidate the frozen report and its deterministic financial outputs."""
    if payload.get("document") != "bo_fo_so_agentic_vertical_slice":
        raise AgenticVerticalSliceError("Unexpected vertical-slice document type.")
    if payload.get("version") != REPORT_VERSION:
        raise AgenticVerticalSliceError("Unsupported vertical-slice version.")
    if "INCOMPLETE" not in payload.get("disclosure", ""):
        raise AgenticVerticalSliceError("Incomplete disclosure is mandatory.")
    if payload.get("final_credit_score") is not None or payload.get("facility_approval") is not None:
        raise AgenticVerticalSliceError("The evaluation slice cannot carry final outputs.")
    expected = content_hash({
        key: value for key, value in payload.items() if key != "report_content_hash"
    })
    if payload.get("report_content_hash") != expected:
        raise AgenticVerticalSliceError("Vertical-slice content hash is invalid.")
    try:
        fo = FinancialOverview.model_validate(payload["financial_overview"]["packet"])
        m3 = M3CorporateAssessment.model_validate(
            payload["structural_overview"]["governed_assessment"])
    except Exception as exc:
        raise AgenticVerticalSliceError(f"Frozen contract validation failed: {exc}") from exc
    expected_calculations = [
        item.model_dump(mode="json") for item in calculate_financials(fo)
    ]
    if payload["financial_overview"]["calculations"] != expected_calculations:
        raise AgenticVerticalSliceError("Frozen financial calculations do not match inputs.")
    status = payload["case"]["transaction_borrower_status"]
    entity_id = payload["case"]["transaction_borrower_entity_id"]
    if status != m3.borrower_identification.status:
        raise AgenticVerticalSliceError("Borrower status differs from governed M3.")
    if status != "identified" and entity_id is not None:
        raise AgenticVerticalSliceError("Unidentified borrower cannot carry an entity ID.")
    if payload["completion"].get("final_credit_synthesis") != "blocked":
        raise AgenticVerticalSliceError("Final credit synthesis must remain blocked.")
    return payload


def _pdf_text(value: Any) -> str:
    text = "" if value is None else str(value)
    for before, after in {
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u2192": "->",
    }.items():
        text = text.replace(before, after)
    return text.encode("latin-1", "replace").decode("latin-1")


def write_agentic_vertical_slice_pdf(payload: dict[str, Any], path: Path) -> None:
    """Render an audit-rich PDF using the repository's available ReportLab path."""
    validate_agentic_vertical_slice(payload)
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
            TableStyle, XPreformatted,
        )
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise AgenticVerticalSliceError("PDF output requires reportlab.") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "VTitle", parent=styles["Title"], fontSize=19, leading=23,
        textColor=colors.HexColor("#17324d"), spaceAfter=8,
    )
    h1 = ParagraphStyle(
        "VH1", parent=styles["Heading1"], fontSize=14, leading=17,
        textColor=colors.HexColor("#17324d"), spaceAfter=5,
    )
    h2 = ParagraphStyle(
        "VH2", parent=styles["Heading2"], fontSize=10, leading=12,
        textColor=colors.HexColor("#315b7d"), spaceBefore=7, spaceAfter=3,
    )
    body = ParagraphStyle("VBody", parent=styles["BodyText"], fontSize=8, leading=10)
    small = ParagraphStyle("VSmall", parent=body, fontSize=6.8, leading=8.2)
    warning = ParagraphStyle(
        "VWarning", parent=body, backColor=colors.HexColor("#fff3dc"),
        borderColor=colors.HexColor("#cc7e16"), borderWidth=.7,
        borderPadding=6, textColor=colors.HexColor("#7a3e00"), spaceAfter=7,
    )
    code = ParagraphStyle(
        "VCode", fontName="Courier", fontSize=5.4, leading=6.7,
        borderColor=colors.HexColor("#c8d2dc"), borderWidth=.4,
        borderPadding=5, backColor=colors.HexColor("#f5f7f9"),
        splitLongWords=True,
    )

    def para(value: Any, style=body):
        return Paragraph(escape(_pdf_text(value)).replace("\n", "<br/>"), style)

    def block(value: Any):
        rendered = json.dumps(value, indent=2, ensure_ascii=False, default=str)
        wrapped: list[str] = []
        for line in _pdf_text(rendered).splitlines():
            indent = line[:len(line) - len(line.lstrip())]
            wrapped.extend(textwrap.wrap(
                line, width=145, subsequent_indent=indent + "  ",
                replace_whitespace=False, drop_whitespace=False,
                break_long_words=True, break_on_hyphens=False,
            ) or [""])
        return XPreformatted(escape("\n".join(wrapped)), code)

    def table(rows: list[list[Any]], widths: list[float], *, header=True):
        converted = [[para(cell, small) for cell in row] for row in rows]
        result = LongTable(converted, colWidths=widths, repeatRows=1 if header else 0)
        commands = [
            ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#b7c4cf")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        if header:
            commands += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ]
        result.setStyle(TableStyle(commands))
        return result

    case = payload["case"]
    bo = payload["business_overview"]
    fo = payload["financial_overview"]
    so = payload["structural_overview"]
    governed = so["governed_assessment"]
    story: list[Any] = [
        Paragraph("BO / FO / SO Thin Vertical Slice", title),
        para(payload["disclosure"], warning),
        para(
            f"{case['reporting_entity_name']} | case {case['case_id']} | "
            f"as of {case['as_of_date']}"
        ),
        Spacer(1, 3 * mm),
        Paragraph("What this test proves", h1),
        para(
            "The report carries the actual recorded live BO specialist outputs through "
            "a deterministic integration with the existing simulated FO fixture and an "
            "offline Validator 7 replay of the final M3 proposal. It preserves provenance, "
            "qualifications, exclusions, dependencies, and the borrower hard gate."
        ),
        Paragraph("Completion state", h2),
        table(
            [["Stage", "State"]] + [
                [key.replace("_", " "), value]
                for key, value in payload["completion"].items()
            ],
            [65 * mm, 110 * mm],
        ),
        Paragraph("Borrower gate", h2),
        para(
            f"Status: {case['transaction_borrower_status']}. "
            f"{case['transaction_borrower_limitation']}"
        ),
        Paragraph("Required next steps", h2),
    ]
    story.extend(para(f"- {item}") for item in payload["required_next_steps"])

    metrics = bo["metrics"]
    story.extend([
        PageBreak(), Paragraph("Executive analytical overview", h1),
        para(payload["disclosure"], warning),
        Paragraph("Actual BO run", h2),
        table([
            ["Measure", "Recorded value"],
            ["Live model", ", ".join(bo["meta"].get("models") or [])],
            ["Specialists", metrics.get("agents_validated")],
            ["Model calls", metrics.get("model_calls")],
            ["Recorded / usable hypotheses", f"{metrics.get('hypotheses_recorded')} / {metrics.get('hypotheses_usable')}"],
            ["Admissibility", json.dumps(metrics.get("by_admissibility") or {}, sort_keys=True)],
            ["Coverage addressed", f"{metrics.get('coverage_addressed')} of {metrics.get('coverage_checks')}"],
            ["Citation failures / recovered", f"{metrics.get('citation_verification_failures')} / {metrics.get('citations_recovered')}"],
            ["Tokens", f"{bo['usage'].get('total_tokens')} total"],
        ], [75 * mm, 100 * mm]),
        Paragraph("High-materiality usable BO findings", h2),
    ])
    for finding in bo["high_materiality_usable_findings"]:
        story.extend([
            para(f"{finding['hypothesis_id']} - {finding['claim']}", body),
            para(
                f"{finding['direction']} | {finding['disposition']} / "
                f"{finding['admissibility']} | {finding['confidence']} | "
                f"evidence: {', '.join(finding['supporting_evidence_ids']) or 'none'}",
                small,
            ),
        ])
    story.append(Paragraph("Cross-domain observation", h2))
    if not payload["cross_domain_findings"]:
        story.append(para("No supported BO-to-FO monitoring link was emitted."))
    for item in payload["cross_domain_findings"]:
        story.extend([
            para(item["text"]),
            para(
                f"Status: {item['status']} | BO: {', '.join(item['bo_hypothesis_ids'])} | "
                f"FO: {', '.join(item['financial_metric_refs'])}",
                small,
            ),
        ])

    packet = fo["packet"]
    periods = packet["periods"]
    story.extend([
        Paragraph("Financial Overview - simulated fixture", h1),
        para(payload["disclosure"], warning), para(packet["notice"], warning),
        para(fo["fixture_binding"]["reason"]),
        Paragraph("Invented financial inputs (USD million)", h2),
    ])
    fact_names = list(periods[0]["facts"])
    story.append(table(
        [["Input", *[f"FY{period['fiscal_year']}" for period in periods]]] + [
            [name.replace("_", " "), *[
                f"{period['facts'][name]['value']:,.1f}" for period in periods
            ]] for name in fact_names
        ],
        [75 * mm, 50 * mm, 50 * mm],
    ))
    story.extend([Paragraph("Deterministic calculations", h2)])
    story.append(table(
        [["Period", "Metric", "Value", "State", "Formula"]] + [[
            item["period"], item["metric_id"], item["value"], item["state"], item["formula"],
        ] for item in fo["calculations"]],
        [22 * mm, 38 * mm, 27 * mm, 28 * mm, 60 * mm],
    ))
    story.extend([Paragraph("Fixture findings", h2)])
    for finding in packet["findings"]:
        story.append(para(f"{finding['kind']}: {finding['text']} [{finding['provenance']}]"))
    story.extend([Paragraph("Assumptions", h2)])
    story.extend(para(f"- {item}", small) for item in packet["assumptions"])
    story.extend([Paragraph("Missing real financial information", h2)])
    story.extend(para(f"- {item}", small) for item in packet["missing_information"])

    story.extend([
        PageBreak(), Paragraph("Structural Overview - governed M3", h1),
        para(payload["disclosure"], warning),
        para(so["source"]), para(so["limitation"], warning),
        Paragraph("Borrower identification", h2),
        block(governed["borrower_identification"]),
        Paragraph("Validator-admitted legal entities", h2),
    ])
    entity_rows = [["Entity", "Classification", "Identity", "Verification", "Evidence"]]
    entity_rows += [[
        f"{item['legal_name']}\n{item['entity_id']}", item["classification"],
        item["identity_type"], item["verification_status"],
        ", ".join(item["evidence_ids"]),
    ] for item in governed["legal_entities"]]
    if len(entity_rows) == 1:
        entity_rows.append(["None", "-", "-", "-", "-"])
    story.append(table(entity_rows, [50 * mm, 35 * mm, 28 * mm, 28 * mm, 34 * mm]))
    for label, key in (
        ("Ownership relationships", "ownership_relationships"),
        ("Cash-flow locations", "cash_flow_locations"),
        ("Cash-transfer relationships", "cash_transfer_relationships"),
        ("Structural credit findings", "structural_credit_findings"),
        ("Management assessments", "management_assessments"),
        ("Governance screening", "governance_screening"),
    ):
        story.extend([Paragraph(label, h2), block(governed[key])])
    story.extend([
        Paragraph("Information gaps and SO dependencies", h2),
        block({
            "information_gaps": governed["information_gaps"],
            "downstream_dependencies": governed["downstream_dependencies"],
        }),
        PageBreak(), Paragraph("M3 validation audit", h1),
        para(payload["disclosure"], warning),
        para(
            "Rejected records and the original model proposal are audit-only. They "
            "must not be used as legal-entity graph inputs."
        ),
        Paragraph("Validation issues", h2), block(governed["validation_issues"]),
        Paragraph("Rejected items", h2), block(governed["rejected_items"]),
        Paragraph("Replay controls", h2), block(so["replay"]),
    ])

    for number, agent in enumerate(bo["agents"], 1):
        story.extend([
            PageBreak(),
            Paragraph(f"BO agent {number}: {agent['code']} - {agent['title']}", h1),
            para(
                f"Agent {agent['agent_id']} | status {agent['status']} | "
                f"runs {', '.join(agent['agent_run_ids'])}"
            ),
            Paragraph("Parameters after validation", h2),
            block(agent["parameters"]),
            Paragraph("Validator adjustments and issues", h2),
            block({
                "parameter_adjustments": agent["parameter_adjustments"],
                "validator_adjustments": agent["validator_adjustments"],
                "validation_issues": agent["validation_issues"],
            }),
            Paragraph("Coverage and retrieval", h2),
            block({
                "coverage": agent["coverage"],
                "retrieval_attempts": agent["retrieval_attempts"],
            }),
            Paragraph("Validated hypotheses", h2),
        ])
        for finding in agent["hypotheses"]:
            story.extend([
                para(f"{finding['hypothesis_id']} - {finding['claim']}", body),
                block({
                    key: finding[key] for key in (
                        "claim_type", "direction", "disposition", "admissibility",
                        "confidence", "materiality", "credit_mechanism",
                        "supporting_evidence_ids", "contradicting_evidence_ids",
                        "evidence_quotes", "missing_information", "validation_notes",
                        "review_flags", "fo_handoffs", "presentation_basis",
                    )
                }),
            ])
        story.extend([Paragraph("Recorded model call history", h2)])
        for call in agent["calls"]:
            story.extend([
                para(
                    f"Call {call['call_number']} | {call['model_id']} | "
                    f"{call['latency_ms']} ms | prompt {call['prompt_id']}",
                    body,
                ),
                block({
                    "prompt_hash": call["prompt_hash"],
                    "usage": call["usage"],
                    "error": call["error"],
                    "input_summary": call["input_summary"],
                    "structured_output": call["structured_output"],
                }),
            ])
        story.extend([
            Paragraph("Effective specialist instructions", h2),
            para(agent["effective_instructions"], small),
        ])

    story.extend([
        PageBreak(), Paragraph("Sources, lineage, and integrity", h1),
        Paragraph("Source documents", h2),
    ])
    story.append(table(
        [["File", "Role / form", "Report date", "Accession", "SHA-256"]] + [[
            item.get("filename"), f"{item.get('role')} / {item.get('form')}",
            item.get("report_date"), item.get("accession"), item.get("sha256"),
        ] for item in bo["source_documents"]],
        [38 * mm, 37 * mm, 23 * mm, 35 * mm, 42 * mm],
    ))
    story.extend([
        Paragraph("Run metadata", h2), block(bo["meta"]),
        Paragraph("Stage execution", h2), block(bo["stages"]),
        Paragraph("Integrity hashes", h2), block({
            **payload["integrity"],
            "report_content_hash": payload["report_content_hash"],
        }),
        Paragraph("Shared BO rules", h2), para(bo["shared_rules"], small),
    ])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 6.2)
        canvas.setFillColor(colors.HexColor("#9b3b16"))
        canvas.drawString(14 * mm, 10 * mm, "INCOMPLETE | actual BO | simulated FO | offline-governed SO")
        canvas.setFillColor(colors.HexColor("#66788a"))
        canvas.drawRightString(A4[0] - 14 * mm, 10 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title=f"BO FO SO vertical slice - {case['case_id']}",
        author="Deterministic evaluation export",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def write_agentic_vertical_slice_artifacts(
    payload: dict[str, Any], output_dir: str | Path,
) -> tuple[Path, Path]:
    """Write canonical JSON first, revalidate it, then render the PDF."""
    validated = validate_agentic_vertical_slice(payload)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "bo_fo_so_vertical_slice.json"
    pdf_path = output / "bo_fo_so_vertical_slice.pdf"
    json_path.write_text(canonical_json(validated) + "\n", encoding="utf-8", newline="\n")
    frozen = json.loads(json_path.read_text(encoding="utf-8"))
    validate_agentic_vertical_slice(frozen)
    write_agentic_vertical_slice_pdf(frozen, pdf_path)
    return json_path, pdf_path
