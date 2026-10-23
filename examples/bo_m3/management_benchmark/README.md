# Module 3 senior-management claim benchmark

A small set of **material senior-management, governance and corporate-structure
claims** for Module 3 (Corporate Structure & Senior Management). Each case is
built from a **verbatim excerpt of the tracked HCA FY2025 Form 10-K bundle**
(the primary filing plus Exhibits 21 and 22). The excerpts were located by
normalising the source HTML to text (strip tags, unescape HTML entities,
collapse whitespace); `manifest.json` records the source path and `sha256` for
each cited file.

## What each case carries

Every case records **two separate labels**, because credibility and credit
direction are assessed independently:

- `credibility.expected` — whether the specific claim is entailed by the cited
  passage (`supported` / `contradicted` / `insufficient`). This mirrors the
  Validator 7 / semantic-entailment posture: a citation id alone is not
  entailment, and a stated intention is not an outcome.
- `credit_direction.label` — the credit sign of the underlying development
  (`negative` / `neutral` / `positive`). A **credible** management explanation
  of an **adverse** development does not turn it into a positive; see the
  `credible_adverse` case (`M3-03`).

The set deliberately includes:

- a **credible-adverse** case (`M3-03`): a credible, faithfully-restated
  staffing/labour risk that remains **credit-negative**; and
- a **correct-topic, unsupported causal/outcome attribution** case (`M3-05`):
  the succession-planning disclosure is real, but the claim attributes a
  measured outcome the filing does not state, so credibility is `insufficient`.

## Grounding status

All eight cases are **REAL-admitted** verbatim HCA 10-K excerpts (no
SCRIPTED/MOCK cases). A verification step confirms every passage substring is
present in the normalised text of its cited file before use.

## Caveats (please read)

- Labels are **developer-authored and awaiting external review**
  (`label_status: developer_authored_awaiting_external_review`). This is **not**
  independent ground truth.
- The expected labels are **frozen**; do not tune implementation rules on these
  cases and then report them as held out.
- Any LLM-generated interpretation of these cases is **PROVISIONAL**. A passing
  scripted/mocked evaluation is **not** proof of real-world correctness, and the
  evaluation is neither perfect nor complete.
