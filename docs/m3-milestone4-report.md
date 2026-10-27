# Milestone 4 — claim-level evidence and Validator 7

Implemented in the existing `m3-rebuild` worktree, preserving prior uncommitted
work. No new paid calls, commits, pushes or merges were made. Other modules'
validator implementations were not changed.

The unchanged Milestone 3 Gemini response now preserves all seven current
appointment facts instead of rejecting entire profiles. The management result
remains partial: real citation errors remain excluded, and all four qualitative
judgments carry explicit qualifications from an authored offline Validator 7
fixture. This does **not** verify live Validator 7 model performance.

## Before and after

| Evidence/result | Milestone 3 saved replay | Milestone 4 saved replay |
|---|---|---|
| Current appointment facts | 0 admitted through the whole-profile gate | 7 independently accepted |
| Roster identities | Whole-profile rejection hid usable identities | All 7 preserved |
| Prior-role claims | Whole-profile rejection | 16 accepted, 1 qualified, 1 unsupported |
| External-recruitment quotation | Whole-profile rejection | 1 unsupported; not silently repaired |
| All evidence claims | No independent claim outcomes | 45 accepted, 1 qualified, 2 unsupported |
| Principal individual judgments | Withheld with profile/fact cascade | Hazen, Marks and McAlevey: qualified |
| Team judgment | Withheld | Qualified |
| Overall | Unavailable | Cautious continuity positive, with explicit evidence/outcome limitations |
| Corporate sibling | Complete | Complete |

The 48 evidence claims comprise the roster date, seven names, seven positions,
seven current appointments, eighteen prior roles, seven submitted quoted facts
and one external-history/recruitment assertion. Qualitative scope is three
principal individuals plus the management team; seven individual credit judgments
are not required.

### Genuine failures retained

- `executive:1:role:2`: Hazen's COO role quotation starts in the preceding
  canonical chunk but cites only the next chunk. It does not match its declared
  source ID. The role assertion is unsupported under that submitted citation;
  the independently correct CEO fact and other supported roles survive.
- `executive:6:external`: McAlevey's external-history quotation crosses the
  physical page 35–36 boundary while citing a page-35 passage only, and does not
  match that passage. It remains unsupported and is not used to infer current
  external recruitment. The internal legal role and current appointment survive.
- `executive:4:role:3`: Foster's primary Division President quotation spans a
  chunk boundary under one ID and is rejected. The original answer also explicitly
  supplied correctly attributed fragments under both canonical IDs. Those
  surviving fragments support the role/date claim with a qualified outcome;
  the failed primary reference is still visible in the ledger.

### Previous false rejections and cascades corrected

Berres' `H uman`, Cuffe's `P hysicianServices` and Marks' `F inancefrom` are
traceable PDF word-boundary artifacts, not changed titles. M3's closed vocabulary
normalization permits semantic comparison while preserving the original quotation
and source text. Mosier's pronoun-based prior role is bound to her own biography
using source offsets, rather than rejected for omitting her full name from every
sentence. A quotation in another executive's biography still fails attribution.
Employer scope is grounded in explicit company/entity context or independently
accepted concurrent company-office evidence. It is not inferred from tenure.

## Qualitative result and dependency handling

| Judgment | Reviewed result | Reason and limitation |
|---|---|---|
| Hazen | Qualified credit positive | Accepted CEO history and independently accepted internal operating/finance roles may support continuity of knowledge. The bad additional COO citation is excluded; competence and realized financial effects are not established. |
| Marks | Qualified neutral | CFO appointment and accepted finance roles support internal-transition context. The original statement that finance roles began in 1996 goes beyond the evidence: a company-joining date does not date the start of finance responsibilities. The reviewer supplies separate limited wording. |
| McAlevey | Qualified neutral | Current expanded responsibilities and a supported internal legal role justify transition monitoring. The bad external-history citation is not used; effectiveness or adverse financial outcomes are not inferred. |
| Team | Qualified credit positive | Several accepted internal career paths may support continuity. The reviewer explicitly adds accepted role-claim references; the original appointment facts alone do not prove decades of cumulative experience, stability or the ability to offset sector pressures. |

The resulting overall assessment is a **cautious continuity positive**, with
Marks and McAlevey treated as neutral transitions. Missing performance/incentive
evidence, citation gaps and unverified financial effects remain explicit. It is
not a competence assessment, governance rating, credit score or demonstrated
financial benefit. The original positive model assessment remains retained
separately from the reviewed overall assessment.

Rejected facts do not poison unrelated facts or profiles. Validator 7 receives
all claim outcomes and reassesses each judgment's surviving basis. Its selected
support IDs must refer to accepted or qualified evidence claims. Invalid references,
wrong-executive support and missing individual identity withhold the affected
judgment. Changes to the original basis or wording are explicitly qualified.
The overall assessment can reference only surviving judgments. Mixed-quality
integration tests exercise accepted, qualified and unsupported judgments together;
no review or invalid review preserves evidence but withholds qualitative output.

## Citation accuracy and traceability

Each match records the submitted quotation, unchanged matched source text,
matching method, original document ID and SHA-256, physical PDF page, canonical
passage ID and page-text character offsets. Normalized text is a separate
comparison view. Matching never searches another passage to accept an incorrect
reference; any matches elsewhere are diagnostic only. Explicitly supplied
fragments retain their individual IDs.

Allowed normalization is whitespace/line breaks, equivalent typographic characters,
and a closed set of PDF word-boundary repairs. There is no fuzzy spelling matching,
letter or digit substitution, name correction, title substitution or negation
removal. Ambiguous hyphenated words and names are not merged. Numerical substring
matching is bounded so, for example, `2024` cannot match `20240` as the same value.
Tests trace accepted spans back to the exact canonical passage text and verify
that page offsets and document identity agree with the shared packet.

The document SHA-256 is
`64fa678981afe7c451820e9f59fc44ab41373702edc9d9175a6ec8a289df45a6`.
The unchanged raw response SHA-256 is
`31979bba87dabe512dbe308fe3f5034ef66286b1beffe9dea747cea5df768c68`.

## Architecture and artifacts

The replay uses `BusinessOverviewRunner.run_case(package=...)`: real case
preparation → shared admission → canonical snapshot → shared routing → saved
specialist response → claim validation → `m3_validator_7` through AgentRuntime,
LLMClient, PromptRegistry, ModelRun/AgentRun and audit persistence → normal M3
artifact/BO handoff. M3 execution does not reopen the PDF. Both model backends and
all unrelated BO agents are offline; the replay records zero paid requests.

The compact final contract is `m3-management-assessment-3.0`. The specialist's
response contract and original raw answer are unchanged. Historical v1/v2 outputs
remain readable under their original versions. The legacy whole-profile validation
implementation has been removed from the active code; it is not a selectable backup.

Readable comparison (all 48 evidence claims, each citation/location, before/after
outcomes, reasons, dependency effects, and all four original/reviewed judgments):

- `output/m3_milestone4/comparison/comparison.html`
- `output/m3_milestone4/comparison/comparison.json`

Final replay directory:
`output/m3_milestone4/hca_final_replay/`

Final component artifacts:
`output/m3_milestone4/hca_final_replay/output/m3_rebuild/HCA_M3_CREDIT_2024/run_8e79c644eecd/`

- `management_response.txt`: byte-identical original Gemini answer.
- `management_extraction.json`: unchanged parsed specialist JSON.
- `management_claim_validation.json`: all independent evidence/judgment outcomes.
- `Senior Management Assessment.json`: partial final assessment with usable claims.
- `validator7_request.json`, `validator7_response.txt`, `validator7_review.json`:
  exact review input, offline fixture response, model/agent/prompt provenance.
- `management_validation.json`, diagnostics and independent component manifest.

The parent replay directory preserves the actual `case.db`, canonical snapshot,
M3 packet, source preflight and summary. The original Milestone 3 artifacts remain
untouched. The offline review fixture is
`examples/m3_rebuild/hca_validator7_m4_offline.json`, explicitly labelled as an
authored fixture rather than live model output.

Implementation is scoped to `app/services/bo/m3_rebuild/` (matching, claims,
Validator 7, workflow, comparison and replay harness), M3 injection in the BO
runner, and the M3 v3 rendering entry. Other modules' validator implementations
and common provider/LLM runtime implementations were not changed. Existing test
expectations of whole-profile rejection were replaced with claim-level assertions;
the source/date/employer/citation safeguards remain exercised.

## Verification and remaining gaps

Full offline suite: **1,860 passed, 129 skipped**, one existing Starlette/httpx
deprecation warning. Skips remain environment-dependent local data/OCR/rendering
or explicitly disabled live-provider tests. Focused M3, registry, normal BO and
existing Module 4 integration checks passed. `git diff --check` passes.

The offline reviewer fixture verifies the architecture, reference guards and
handling of its explicit qualitative decisions. It does not establish the
reasoning accuracy, reliability or calibration of a live Validator 7 model.
Normalization is deliberately bounded; unsupported layouts, unlisted split words
or ambiguous employer evidence may still require review. Character spans locate
captured page text rather than claiming PDF bounding-box coordinates.

Cross-module challenge/rerun scheduling and observation-level revision lineage
remain separate work. Existing downstream adapters carry the limited reviewed
wording and citations, but do not independently challenge Validator 7 or fully
render every new claim/review metadata field. The HCA test still uses a declared
availability timestamp at the cutoff rather than independently verified SEC
publication metadata. Corporate simulation disclaimers remain intact. No final
credit decision or human approval is asserted.

## Subsequent closeout

The [Milestone 4 closeout](m3-milestone4-closeout.md) records the subsequent live-review inspection, explicit saved-output corrections and final offline verification. Earlier artifacts and raw answers remain unchanged.
