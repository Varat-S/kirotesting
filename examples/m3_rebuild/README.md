# Module 3 rebuild references and verified offline fixture

`hca_input.json` and `hca_reference.json` are byte-identical copies of the two
explicitly linked user references. They contain simulated educational corporate
assumptions, not verified HCA legal information.

`hca_2024_extraction.json` is a historical v1 facts-only manually transcribed
fixture verified against the selected local FY2024 PDF. It is not a Gemini
response and cannot satisfy the current v2 facts-and-credit-judgments contract.
Tests explicitly add authored offline judgments rather than interpreting that
historical file as model analysis. Previously generated v1 assessments retain
their historical schema and are not relabelled v2.
`hca_2024_sources.json` preserves the selected cover and executive section page
text, SHA256 and physical page locations for portable offline tests.
`hca_fixture_provenance.json` documents fixture origin and extraction limitations.
Do not substitute FY2025 officers or describe this fixture as a successful live run.

`hca_validator7_m4_offline.json` is an explicitly authored offline qualitative
review fixture for the unchanged Milestone 3 Gemini response. It qualifies the
three principal individual judgments and the team judgment, citing independently
accepted evidence claims. It is not a live Validator 7 model response, a general
review algorithm or an expected extraction answer supplied to Gemini.

See ../../docs/m3-rebuild.md for running each component and selecting the real BO
replacement. Final generated local artifacts and the full preview are in
`output/m3_rebuild/HCA_2024/run_verified_offline` for this session; outputs are
Git-ignored and reproducible with the documented command and selected local PDF.
