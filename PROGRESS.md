# ReviewHarness Progress

Last updated: 2026-07-12T16:05:48+09:00
Current phase: P0 LOCALLY COMPLETE; LIVE EVENT/HOSTED PROVIDER UNVERIFIED
Current checkpoint: every locally verifiable P0 acceptance criterion passed with final CLI, evaluator, test, static-analysis, and rendered-report evidence
Blocked: NO

## Current repository state

- Branch: `main`
- Implementation commit: `d56efdc` is the last committed checkpoint; the final verified hardening, evidence, report, README, and submission package are pending one final commit
- Working tree: the intended staged set contains blocking kernel/runner/security hardening, paired regressions, one canonical synthetic runtime run, final report, and submission text; duplicate runtime reruns, `tmp/`, `[중요]대회주요정보.txt`, and `tools/` are excluded
- Python/runtime: CPython 3.12.10, `uv` 0.11.17
- Model configuration: deterministic offline `LocalHeuristicProvider`; no model key or arbitrary reviewer tools
- Network assumptions: local P0 verification is offline; the typed Ralphthon adapter is not exercised against an authenticated live event account

## Verified baseline

- Commands: the exact single-paper, security/quality, and batch/runtime acceptance commands were attempted before package implementation
- Result: all failed with exit code 1 because `reviewharness` did not exist
- Runtime: not measured at RED because execution never reached the kernel
- Passing tests: none at RED
- Failing evidence:
  - `.omo/evidence/reviewharness-p0/C001-single-paper.RED.txt`
  - `.omo/evidence/reviewharness-p0/C002-security-quality.RED.txt`
  - `.omo/evidence/reviewharness-p0/C003-batch-runtime.RED.txt`
  - `.omo/evidence/reviewharness-p0/scaffold-help.RED.txt`
- First green CLI receipt: `.omo/evidence/reviewharness-p0/scaffold-help.txt`

## Final acceptance receipts

- C001: the exact full-mode single-paper `review` command completed, wrote the complete per-paper artifact set, and the exact `validate` command passed.
- C002: the exact `eval-security` and `eval-quality` commands passed and wrote `evals/results/security.json` and `evals/results/quality.json`.
- C003: the exact ten-paper `batch`, `eval-runtime`, full `pytest`, and report-build commands passed.
- Final runtime-debug audit: ingest cancellation exited before the blocking worker was released; an unexpected paper-local reviewer error produced exactly one `review_failed` completion without terminating a later sibling; ambiguous provider targets remained unlinked; and all six exact injection commands were quarantined before provider input. The focused 18-test lane passed in 0.65 seconds.
- Full tests: 198 passed in 10.97 seconds.
- Ruff format: 59 files already formatted.
- Ruff check: all checks passed.
- Basedpyright: 0 errors.
- Final report: `submission/reviewharness_technical_report.pdf`, visually inspected page by page, 4 pages, 8,755 bytes, SHA-256 `e5daac26b29eec8046f44943b10c90a1c9038aa6090d00d9c39dc5a38ae781cb`, anonymous Author metadata, and human correlation stated as N/A.

## P0 status

- [x] Strict input and output schemas
- [x] Single-paper valid output
- [x] Page-aware parsing
- [x] Claim ledger
- [x] Evidence-grounded findings
- [x] Unsupported-finding rejection
- [x] Rubric-based score calibration
- [x] Constructive comment
- [x] Prompt-injection isolation
- [x] Output security validation
- [x] Full mode
- [x] Fast mode
- [x] Ten-paper bounded runner
- [x] Deadline controller
- [x] Failure isolation
- [x] README instructions
- [x] Technical-report source and four-page render path

## Current best measurements

All numbers below come from current files in `evals/results/`. They measure controlled synthetic fixtures with the deterministic offline provider, not private event papers, a live hosted model, or human-reviewer agreement.

### Quality proxies

- Evidence coverage: 1.0 over 5 controlled cases
- Unsupported critique rate: 0.0
- Controlled-fixture issue recall: 1.0
- Controlled-fixture issue precision: 1.0
- Supported minority-finding preservation: 1.0
- Score-comment consistency: 1.0
- Repeatability: 1.0
- Top-issue stability: 1.0
- Valid completion: 1.0
- Evaluator duration: 0.030016 seconds
- Artifact: `evals/results/quality.json` (`passed: true`)

### Security

- Attack success rate: 0.0 over 12 controlled cases
- Marker leakage rate: 0.0
- Unauthorized tool calls: 0
- Trusted-ID invariance: 1.0
- Valid completion: 1.0
- Clean-vs-injected score delta: 0.0
- Clean-vs-injected issue overlap: 1.0
- Detection recall: 1.0
- Benign false-positive rate: 0.0
- Evaluator duration: 0.047 seconds
- Scope: `deterministic_synthetic_fixture_and_provider`
- Artifact: `evals/results/security.json` (`passed: true`)

### Runtime

- Batch completion: 10/10 valid
- Total runtime: 0.454 seconds
- p50 paper runtime: 0.180 seconds
- p95 paper runtime: 0.281 seconds
- Timeouts: 0
- Retries: 0
- Primary-run fast fallbacks: 0
- Invalid outputs: 0
- Full mode executed: YES (5 papers)
- Fast mode executed: YES (5 papers)
- Monotonic deadline controller: PASS at 1,500 seconds
- Configured concurrency: 5 papers / 10 model calls
- Failure isolation: PASS in a separate 3-paper scenario; `SAMPLE-001` recovered from a forced full-mode `RuntimeError` through fast fallback while siblings completed
- Artifact: `evals/results/runtime.json` plus `evals/results/runtime-artifacts-153841671000000/`

### Human alignment

- Real human labels available: NO
- Actual human correlation: N/A
- Development proxies used: rubric-anchor compliance, evidence coverage, unsupported-critique rejection, controlled issue precision/recall, minority preservation, score-comment consistency, repeatability, top-issue stability, injection containment, trusted-ID invariance, valid completion, failure isolation, and runtime

## Latest iteration

- Hypothesis: fail-closed claim relinking, terminal exception receipts, and complete lexical quarantine close the final submission-blocking proof gaps without changing review methodology
- Changed component: trusted claim-link boundary, paper-local completion receipt, and secure-ingest phrase detection only
- Evaluation command: focused kernel/runner/security regressions, one full pytest run, all three frozen evaluators, report build, four-page visual inspection, and diff checks
- Before: ambiguous targets could inherit a sole central claim, unexpected paper exceptions lacked a terminal completion record, and three exact reviewer commands reached provider evidence
- After: ambiguous targets remain unlinked, every exceptional paper emits exactly one typed terminal record, all six exact commands are quarantined, the focused lane passed 18 tests, and the full suite passed 198 tests
- Security effect: fresh controlled evaluation reports zero attack success, zero marker leakage, zero unauthorized tool calls, and full trusted-ID invariance
- Runtime effect: the fresh ten-paper synthetic run completed 10/10 in 0.454 seconds; hosted-provider latency remains unverified
- Decision: KEEP

## Decisions

| Time | Decision | Evidence | Consequence |
|---|---|---|---|
| 13:59 | Preserve the missing-package run as the RED baseline | `.omo/evidence/reviewharness-p0/C00*.RED.txt` | Later green claims remain falsifiable and do not erase the initial failure |
| 14:29 | Use strict typed schemas and monotonic deadlines as the core boundary | `a138bb8` | Trusted identifiers, score ranges, and deadline state are application-owned |
| 14:35 | Treat extracted PDF content as quarantined evidence | `2f449d5`, `97fe6e6` | Paper instructions do not become control-plane instructions |
| 14:41 | Resolve findings by evidence and central-claim impact, not majority vote | `1bee13a` | Supported minority findings survive; unsupported findings are rejected |
| 14:46 | Use the deterministic offline provider for locally reproducible P0 proof | `8defcc7` | Local evaluation requires no key; live-model quality remains unverified |
| 15:04 | Keep paper/model concurrency bounded and failures paper-local | `a536ef8` | The ten-paper path streams completions and one failure does not cancel siblings |
| 15:10 | Stop optional feature work and freeze the measured local P0 configuration | `evals/results/*.json` | Remaining work is validation, state/report finalization, and handoff only |

## Assumptions

- Event API details remain isolated behind `src/reviewharness/api_adapter.py`.
- The internal `overall_recommendation` and `comment` fields translate only at the event boundary to `overall` and `comments`; server-owned `ordinal` remains outside reviewer control.
- Real human judge labels and private heuristics are unavailable during development.
- The published 16:35-17:00 window is the production hard limit, represented locally as a 1,500-second monotonic deadline.

## Blockers and limitations

- No local P0 blocker is currently known.
- Authenticated assignment retrieval, PDF download, review submission, receipt/idempotency behavior, final live response envelopes, and provider rate limits remain unverified without event credentials.
- The deterministic offline provider proves local contracts and orchestration, not quality on arbitrary private papers or a hosted model.
- PyMuPDF text extraction inspects metadata, links, annotations, and embedded-file descriptors but does not perform OCR or image/QR semantic analysis.
- External novelty and unchecked technical details remain uncertain without literature access; the kernel must express that uncertainty.

## Next exact action

1. Commit and push the verified frozen state, judge-facing README, controlled evaluation evidence, and anonymous submission artifacts; keep private event inputs and unrelated untracked files out of Git.
2. Manually enter the prepared OpenReview fields, select the four-page PDF, submit before 16:30 Asia/Seoul, and verify the platform confirmation/status.
