# Milestone 4 closeout

Milestone 4 is ready to close before downstream synthesis/challenge integration
in Milestone 5. This closeout made zero paid calls. Changes remain uncommitted;
other modules' validators are unchanged. Management remains `partial` because
real citation failures and disclosure limitations remain, not because it needs a
recurring review label.

| Finding | Before: saved live reviewer | After: explicit review correction |
|---|---|---|
| Marks (`jud_02`) | Accepted finance roles “since 1996” | Qualified: CFO in May 2024 after disclosed internal finance roles dating to July 2004. The joining date does not establish finance-role tenure. Neutral transition context; execution and financial benefit unestablished. |
| Team (`jud_04`) | Accepted decades of experience with roster/current-appointment references | Qualified: several accepted career histories may support continuity of company knowledge. Added operating, finance, HR and clinical role references; competence, performance and protection from sector pressures are unestablished. |
| Hazen / McAlevey | Qualified | Unchanged, including their references and qualifications. |
| Overall | Strong continuity-positive wording | Limited continuity positive using the corrected history, neutral transition monitoring and retained labor, regulatory and debt-service qualifications. |

These are explicitly authored corrections, **not live Validator 7 decisions**.
The source hash, original judgments hash and original reviewer content hash bind
this correction to the saved answer. The generic M3 correction path can qualify
or withhold judgments, but cannot admit facts or cite rejected evidence. Specific
HCA wording lives only in `examples/m3_rebuild/hca_m4_explicit_review_corrections.json`.
A qualification supplies separate narrower wording and a concise reason; it cannot
make an unsupported factual assertion acceptable merely by flagging it. Original
specialist wording and reviewer decisions remain in the audit trail.

Every surviving claim now links directly through `citation_ids` to the artifact's
citation table. Document ID/hash, physical page, canonical passage ID, original
matched text and page-text offsets remain accessible. Matching/admission rules
are unchanged. The two incorrect submitted claims (`executive:1:role:2` and
`executive:6:external`) stay unsupported; Foster's independently supported
fragment-based claim stays qualified. All seven profiles remain usable.

Normal BO replay used real case preparation, admission, canonical snapshot and
routing, the unchanged specialist answer, the unchanged saved live reviewer answer,
shared reviewer execution/audit, and the explicit correction overlay. Raw responses
were byte-checked against the prior live run. M3 execution still consumes only
routed evidence. The existing shared BO runner adds only an optional M3 correction
argument; other modules receive no changed inputs or validator behavior.

Final replay: `output/m3_milestone4/closeout_final_replay/`.
Final component: `output/m3_milestone4/closeout_final_replay/output/m3_rebuild/HCA_M3_CREDIT_2024/run_0ac532bf4380/`.

- `closeout_comparison.md`: before/after wording and every corrected supporting source passage.
- `closeout_verification.json`: 16 passed automated checks plus separately labelled assistant review.
- `validator7_response.txt`, `validator7_review.json`: unchanged raw saved live answer and parsed replay.
- `validator7_explicit_corrections.json`: authored changes, original decisions, reasons and old/new overall text.
- `Senior Management Assessment.json`, claim validation and manifests: corrected normal M3 output.
- `case.db`, canonical snapshot and M3 packet: preparation, routing and shared execution provenance.

Verification: **183 passed**, one existing Starlette/httpx deprecation warning.
This includes M3 integration, mixed claim quality, formatting/citation safeguards,
stale/invalid correction rejection, independent findings, normal BO preparation,
Module 4 and validator identity checks. HCA automated checks establish source-span
traceability and reference membership; my review separately checks the narrowed
wording against the cited career history. No new live reasoning test was performed.

Cross-module synthesis/challenge, observation revision lineage and fuller downstream
rendering remain Milestone 5. The original single live review's reasoning failures
remain recorded; this closeout does not establish general reviewer reliability.
The HCA source availability timestamp remains declared test metadata. Corporate
simulation status is unchanged. The general drafting-aid limitation is documented
once in the root README; no recurring assessment label was added.
