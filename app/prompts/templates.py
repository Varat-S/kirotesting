"""Versioned prompt templates for the LLM methods (task 6.2, 6.3, 6.4, 6.5).

Each template is paired with the FIXED response JSON Schema its output is
validated against (Req 19.4). The prompt text makes the hard constraints
explicit so a real provider is held to the same rules the deterministic fake
backend obeys in tests:

* extraction: no unsupported inference; attach source IDs; never auto-verify
  (Req 3.4, 3.6).
* analysis: do NOT invent information, do NOT infer missing values, distinguish
  fact from interpretation, use ONLY supplied evidence, cite evidence IDs,
  surface contradictions, and state when a conclusion cannot be reached
  (Req 12.4). Missing critical evidence yields a caveat/question rather than
  confident prose (Req 12.7).
* challenge: test support, metric contradictions, omitted risks, mitigant
  relevance, missing evidence, alternative explanations and overstated certainty
  (Req 13.1); it is additive and never rewrites the analysis (Req 13.3).

Prompts are DATA here. The :mod:`app.prompts.registry` loads them into the
versioned ``prompt_versions`` store and hashes them.
"""

from __future__ import annotations

from app.schemas.llm import (
    ANALYSIS_JSON_SCHEMA,
    CHALLENGE_JSON_SCHEMA,
    EXTRACTION_JSON_SCHEMA,
)

# Role identifiers map a prompt to the LLMClient method it drives.
ROLE_EXTRACT = "extract"
ROLE_ANALYZE = "analyze"
ROLE_CHALLENGE = "challenge"


QUALITATIVE_EXTRACTION_TEMPLATE = (
    "You extract qualitative borrower facts from the supplied evidence ONLY.\n"
    "Rules:\n"
    "- Use ONLY the supplied source snippets; never invent or infer missing "
    "values.\n"
    "- Every fact MUST reference at least one source ID (document/page).\n"
    "- Return fact name, statement, period, source ref, and a confidence.\n"
    "- Never mark a fact 'verified'; use 'unverified' (or an explicit "
    "non-value status). Verification is deterministic and happens later.\n"
    "- Cover: management, ownership, corporate structure, competitive position, "
    "industry risks, and qualitative debt terms where present.\n"
    "Return structured JSON matching the response schema."
)

BUSINESS_ANALYSIS_TEMPLATE = (
    "You are a credit analyst interpreting ALREADY-VERIFIED evidence. You do "
    "NOT recompute numbers and you do NOT invent facts.\n"
    "Rules:\n"
    "- Use ONLY the supplied canonical facts, deterministic metrics, trends, "
    "benchmarks, snippets, data limitations and open conflicts.\n"
    "- Do NOT invent information and do NOT infer missing values.\n"
    "- Distinguish fact from interpretation on every claim.\n"
    "- Cite evidence IDs on every claim; a factual claim MUST carry evidence.\n"
    "- Surface contradictions and open conflicts; do not paper over them.\n"
    "- If critical evidence is missing, produce a data_limitation and a "
    "question_for_human rather than confident prose.\n"
    "Return structured JSON with business_overview, repayment_analysis, "
    "key_risks, mitigants, data_limitations and questions_for_human."
)

CHALLENGE_TEMPLATE = (
    "You are a second-pass critic. You do NOT rewrite the analysis; you only "
    "raise challenges against it.\n"
    "For each important claim test whether: it is supported by evidence; any "
    "narrative contradicts a deterministic metric; a material risk is omitted; "
    "claimed mitigants are relevant; critical evidence is missing; an "
    "alternative explanation is plausible; or certainty is overstated.\n"
    "Return structured JSON: a list of challenges each with claim_id, "
    "issue_type, severity and reason, plus any supporting evidence_refs."
)


# The registered prompt catalogue: (name, role, template, response_schema).
# ``business_analysis`` surfaces as ``business_analysis_v1.0`` once registered
# (Req 19.1).
PROMPT_CATALOGUE: list[dict] = [
    {
        "name": "qualitative_extraction",
        "role": ROLE_EXTRACT,
        "template": QUALITATIVE_EXTRACTION_TEMPLATE,
        "response_schema": EXTRACTION_JSON_SCHEMA,
        "label": "Qualitative extraction prompt",
    },
    {
        "name": "business_analysis",
        "role": ROLE_ANALYZE,
        "template": BUSINESS_ANALYSIS_TEMPLATE,
        "response_schema": ANALYSIS_JSON_SCHEMA,
        "label": "Generative credit analysis prompt",
    },
    {
        "name": "challenge",
        "role": ROLE_CHALLENGE,
        "template": CHALLENGE_TEMPLATE,
        "response_schema": CHALLENGE_JSON_SCHEMA,
        "label": "Challenge / critic prompt",
    },
]
