# ReviewHarness Progress

Last updated: 2026-07-14T19:53:06+09:00

Phase: post-event provider-contract hardening, publication gate

Branch: `fix/provider-contract-quality-gates`

Blocked: NO; GitHub publication is the remaining external action

## Current verdict

The post-event hardening cycle repaired the scientific-signal boundary without
weakening trusted identity, prompt-injection containment, failure isolation, or
receipt validation.

The repository now has four distinct proof levels:

| Proof level | Current result |
|---|---|
| Deterministic structure and regression | VERIFIED: 241 passed, one opt-in provider test skipped; Ruff and basedpyright green |
| Public provider replay through the kernel | VERIFIED: central claim, major cited concern, `tri_lens` scores, comment trace, and strict final review survived |
| Real Codex provider boundary | VERIFIED only for two concurrent public synthetic requests; `1 passed` in 43.37 seconds, no event API or submission |
| Real-provider ten-paper runtime / human alignment | UNVERIFIED / N/A |

The authenticated event workflow is not revalidated by the public evidence in this
branch. Historical private event material, credentials, PDFs, reviews, and receipts
remain outside Git.

## Why this cycle was needed

A read-only postmortem found that the production-capable path could receive useful
provider candidates and still emit a generic review. The main causes were:

- provider-visible evidence used line identifiers while provider output naturally
  cited paragraph blocks;
- exact locator and quote checks then rejected most scientific signal;
- fast mode allowed a null score proposal;
- provider-success paths could fall back to one fixed score vector; and
- schema-valid generic prose could pass the shallow final sink.

The repository had strong transport and security controls, but those controls did
not prove that provider reasoning survived into the final scientific review.

## Verified milestone commits

| Commit | Milestone | Verified effect |
|---|---|---|
| `5c8ab11` | Static-analysis baseline | Restored Ruff and basedpyright without behavior change |
| `770fec7` | Provider evidence contract | Added parser-owned paragraph blocks and exact block-plus-quote canonicalization |
| `44e8b64` | Score/comment fail-closed gates | Required score provenance, added full calibrator and comment inclusion trace, removed fixed-score success fallback |
| `f1453a8` | Degraded live behavior | Removed automatic heuristic substitution and added typed paper-local live failures |
| `a76d005` | Scoped evaluation | Separated component, replay-conformance, paired-security, local-runtime, and real-provider claims |

## Current saved measurements

The canonical aggregate artifacts are `evals/results/quality.json`,
`evals/results/security.json`, and `evals/results/runtime.json`. They were regenerated
from public fixtures on 2026-07-14.

### Quality

- Scope: `synthetic_component_cases_plus_public_provider_replay`
- Controlled cases: 5
- Evidence coverage: 1.0
- Unsupported critique rate: 0.0
- Issue precision / recall: 1.0 / 1.0
- Minority preservation: 1.0
- Score-comment consistency: 1.0
- Valid completion, repeatability, top-issue stability: 1.0
- Provider-replay conformance: PASS
- Human correlation: N/A
- Aggregate gate: PASS

The public replay trace at
`evals/results/quality-conformance/QUALITY-CONFORMANCE/` contains:

- one canonical central claim at `p1-b2`;
- one retained `minority_supported` major concern at `p1-b4`;
- a score trace sourced from `tri_lens` with Overall Recommendation 2;
- a comment trace including the claim and finding; and
- a validated final review containing the cited concern.

### Security

- Scope: `synthetic_attack_cases_plus_public_paired_documents_local_provider`
- Provider: `local_heuristic_no_tools_no_network`
- Controlled cases: 12; public clean/injected pairs: 1
- Detection recall / trusted-ID invariance / valid completion: 1.0
- Attack success / marker leakage / benign false-positive rate: 0.0
- Clean-injected score delta: 0.0
- Clean-injected issue overlap: 1.0
- Unauthorized tool calls: N/A (`unmeasured_no_instrumented_runner`)
- Aggregate gate: PASS

This lane does not report zero tool calls because it has no instrumented provider
runner that could observe attempts.

### Runtime

- Scope: `local_synthetic_hash_distinct_pdf_batch`
- Provider: `local_heuristic_no_network`
- Public PDFs: 10, with 10 distinct SHA-256 hashes
- Valid completions: 10/10
- Total / p50 / p95: 0.500 / 0.203 / 0.250 seconds
- Timeouts / retries / primary fallbacks / invalid outputs: 0 / 0 / 0 / 0
- Full and fast modes: both executed
- Failure isolation: PASS in a separate forced-failure scenario
- Real-provider ten-paper runtime: UNVERIFIED

The local timing excludes hosted inference and network latency and must not be used
as a production throughput claim. Verbose runtime traces are reproducible and
ignored; the compact JSON is the canonical saved result.

## Real-provider smoke

Executed on 2026-07-14 with saved local Codex authentication:

```powershell
$env:RUN_CODEX_EXEC_SMOKE='1'
uv run python -m pytest -q -p no:cacheprovider `
  tests/integration/test_live_provider.py `
  -k real_two_paper_codex_exec_provider_smoke
```

Result: `1 passed, 5 deselected in 43.37s`.

The test issued two concurrent structured-output requests over public synthetic
evidence and parsed both as `TriLensCandidates`. It did not fetch assignments,
submit reviews, use private PDFs, or measure a ten-paper workload.

## Verification gate

Fresh M4 verification completed with:

```powershell
uv run python -m reviewharness eval-quality --output evals/results/quality.json
uv run python -m reviewharness eval-security --output evals/results/security.json
uv run python -m reviewharness eval-runtime --output evals/results/runtime.json
uv run python report/build_report.py --metrics-dir evals/results `
  --output output/pdf/reviewharness-report.pdf
uv run python -m pytest -q -p no:cacheprovider
uv run ruff check --no-cache src tests report
uv run basedpyright
git diff --check
```

Recorded result before the final documentation-only pass:

- Tests: 241 passed, 1 opt-in smoke skipped
- Ruff: all checks passed
- basedpyright: 0 errors, 0 warnings
- Report: 4 pages, anonymous author metadata, SHA-256
  `3d67c93f328e4b9e45dd199f26f86dfd35ad8cc2b4255f6a57ea824b10af08cd`
- Diff check: passed

The complete gate is rerun once more immediately before publication.

## Preserved controls

- Trusted `paper_id` and event ordinal remain application-owned.
- Paper content remains untrusted evidence with no tools, secrets, network, event
  API, or cross-paper mutation capability.
- Critical and major factual concerns require paper-local support.
- Supported minority findings are preserved rather than majority-voted away.
- Fast mode requires a score proposal; full mode uses a dedicated calibrator.
- Recommendations 1-3 require an included, cited, decision-relevant concern.
- Empty claim ledgers fail as unreviewable.
- Live provider failure cannot trigger automatic local-heuristic submission.
- One transient provider retry is bounded and paper-local.
- One failed paper cannot block siblings or create a submission receipt.

## Known limitations

- Human labels and judge-specific heuristics remain unavailable; correlation is N/A.
- Real-provider ten-paper runtime, full-mode latency, and arbitrary-paper quality are
  unmeasured.
- The current public release does not reproduce authenticated event envelopes,
  credentials, rate limits, idempotency behavior, or receipts.
- OCR, figures, tables as images, and QR semantics are not analyzed.
- External novelty and unchecked technical details require independent sources.

## Next exact action

Run the final deterministic gate, inspect the complete staged file and privacy
boundary, commit the publication artifacts, push the branch, and open a draft pull
request. Do not add ignored runtime traces, private event material, credentials, or
submission receipts.
