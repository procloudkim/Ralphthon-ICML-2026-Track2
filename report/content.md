## Problem
Large language models can generate abundant criticism, but useful peer review requires evidence-supported issue selection, rubric calibration, and resistance to document-borne manipulation. Paper content is treated as untrusted evidence rather than authority.
The design targets an API-native local kernel: trusted assignment metadata and a local PDF enter the system; a strict ICML-style review leaves it. Human labels and reviewer-specific heuristics were unavailable during development.

## Contributions
- Evidence-weighted disagreement resolution preserves a supported minority finding when it affects a central claim.
- Source-and-sink separation prevents paper instructions from controlling identifiers, tools, routing, scores, or output shape.
- Deadline-aware full and fast modes provide bounded review depth with per-paper failure isolation.

## Threat model
Adversarial sources include visible text, hidden text, metadata, annotations, links, attachments, encoded content, and reviewer-directed imperatives. Sensitive sinks include scoring, comments, trusted identifiers, credentials, tools, network access, submission, and cross-paper state.
Reviewer calls receive sanitized paper evidence and a strict schema, but no shell, secrets, arbitrary network, submission capability, or cross-paper mutation. Detection never automatically penalizes scientific scores.
---
## Pipeline
Secure PDF ingest produces page-aware evidence and a security scan. A claim ledger identifies central, supporting, and background claims before specialist perspectives generate candidate findings. Findings are normalized, checked against paper-local locators, resolved, calibrated to the trusted rubric, formatted, and validated.
Full mode runs independent method, evidence, and impact perspectives before deterministic resolution. Fast mode uses one tri-lens perspective with the same evidence, calibration, and security gates.

## Evidence and disagreement
Critical and major factual concerns require a valid page locator, an accurate evidence summary, an affected claim, decision impact, and a recommended check. Unsupported factual criticism is rejected rather than laundered into the final comment.
Agreement changes confidence; evidence strength and central-claim impact change priority. This avoids simple majority voting and preserves verified minority findings.

## Calibration and containment
Final ordinal scores are derived from verified findings and official rubric anchors rather than reviewer-score averaging. Contradiction guards reject score-comment mismatches, while confidence represents assessment certainty rather than paper quality.
Trusted assignment code overwrites model identifiers. Raw suspicious spans never reach calibration. Final validation checks schema integrity, trusted-ID invariance, marker leakage, unauthorized requests, and evidence support.
---
## Protocol
Saved evaluator artifacts are the only source for quantitative statements in this report. Controlled scientific fixtures measure evidence grounding and decision consistency; adversarial fixtures measure containment; a local batch dry run measures reliability and latency.
The report builder strictly parses security.json, quality.json, and runtime.json. Missing fields, extra fields, malformed JSON, out-of-range ratios, and non-finite values stop generation.
---
## Failure analysis
The bounded fallback order retains successful specialist evidence, switches delayed work to fast mode, and emits only schema-valid conservative results. A failed paper remains isolated from sibling workers.
Aggregate evaluator artifacts contain no case-level narrative, so this report does not invent a representative case.

## Limitations
- Human labels and private heuristics were unavailable during development.
- Proxy metrics do not guarantee human agreement.
- Novelty and significance remain partly subjective.
- Short papers may omit evidence needed for deep verification.
- Security scanning cannot prove detection of every visual or encoded attack.
- Post-hoc correlation on a small batch would have high uncertainty.

## Conclusion
ReviewHarness makes its proof boundary explicit: evidence and proxy metrics are measured locally, while claims about hidden human alignment remain unavailable until real labels exist.
