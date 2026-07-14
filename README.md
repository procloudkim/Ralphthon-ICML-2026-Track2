# ReviewHarness

ReviewHarness is a local, API-native ICML review kernel built for Ralphthon Track
2. It turns a trusted paper identifier and a local PDF into strict review JSON
while treating every PDF-derived byte as untrusted evidence.

```text
trusted assignment + local paper PDF
    -> secure ingest and parser-owned paragraph blocks
    -> capability-free reviewer call or deterministic offline provider
    -> exact block-and-quote evidence resolution
    -> rubric calibration and constructive comment
    -> semantic, schema, identity, and security validation
```

## Verified proof boundary

Status as of 2026-07-14:

| Surface | Status | Evidence boundary |
|---|---|---|
| Local contracts and regression suite | Verified | 241 tests passed; one opt-in real-provider test skipped in the default lane |
| Public provider-replay conformance | Verified | Central claim, major cited concern, `tri_lens` score provenance, formatter trace, and final validation crossed the real kernel |
| Local ten-paper runtime | Verified in synthetic scope | 10/10 hash-distinct public PDFs completed with the local heuristic provider |
| Real Codex provider smoke | Verified in two-request scope | Two concurrent public synthetic requests passed the opt-in test; no event submission occurred |
| Real-provider ten-paper runtime | **Unverified** | No current ten-paper hosted-provider measurement |
| Authenticated event execution | Not reverified in this public release | The typed adapter and fail-closed live path exist, but current public artifacts do not reproduce event credentials or receipts |
| Human-reviewer agreement | **N/A** | Human labels and private judge heuristics were unavailable |

Do not read a green local evaluator as proof of hosted-model quality, event
readiness, or human correlation. The current machine-readable scopes live in
[`evals/results/`](evals/results/).

## Quick start

ReviewHarness requires Python 3.12 and
[`uv`](https://docs.astral.sh/uv/).

```powershell
uv sync --locked --python 3.12
uv run python -m reviewharness --help
uv run python -m reviewharness review tests/fixtures/clean/sample.pdf `
  --paper-id SAMPLE-001 --mode full --output runs/qa/single/review.json
uv run python -m reviewharness validate runs/qa/single/review.json
```

Fast mode uses one tri-lens reviewer call behind the same evidence, calibration,
and sink gates:

```powershell
uv run python -m reviewharness review tests/fixtures/clean/sample.pdf `
  --paper-id SAMPLE-001 --mode fast --output runs/qa/fast/review.json
uv run python -m reviewharness validate runs/qa/fast/review.json
```

The local `review` and `batch` commands use the deterministic offline provider.
They require no model key or network access. A valid public result contains exactly:

```text
paper_id, soundness, presentation, significance, originality,
overall_recommendation, confidence, comment
```

`paper_id` always comes from trusted command or assignment input. Dimension scores
are integers from 1 to 4, Overall Recommendation is 1 to 6, and Confidence is 1 to
5.

## Review contracts

Full mode runs three independent scientific lenses and then a dedicated score
calibrator. Fast mode requires one tri-lens response with an explicit score
proposal. Both modes:

- expose sanitized parser-owned paragraph blocks to the provider;
- accept only an exact visible block identifier plus a verbatim quote;
- preserve supported minority findings;
- reject unsupported critical or major factual criticism;
- record score provenance as `tri_lens`, `full_calibrator`, or `local_offline`;
- reject an empty claim ledger;
- require a cited decision-relevant concern for recommendations from 1 to 3; and
- validate comment inclusion, trusted identity, score ranges, and security sinks.

There is no fixed-score fallback on a provider-success path. A provider, evidence,
score-provenance, or semantic failure becomes a typed paper-local failure.

## Ten-paper batch

The public batch manifest is strict and contains exactly ten assignments. Relative
PDF paths resolve from the repository root or manifest directory.

```powershell
uv run python -m reviewharness batch tests/fixtures/batch/assignments.json `
  --output-dir runs/qa/batch --deadline-seconds 1500 `
  --paper-concurrency 5 --llm-concurrency 10
```

The runner uses a monotonic deadline, bounded concurrency, isolated paper state,
fast recovery after a full-mode failure, and an append-only completion stream. One
paper failure does not cancel its siblings; the command exits nonzero unless every
requested review succeeds within the deadline.

## Evaluation

Generate the three saved public metrics with:

```powershell
uv run python -m reviewharness eval-quality --output evals/results/quality.json
uv run python -m reviewharness eval-security --output evals/results/security.json
uv run python -m reviewharness eval-runtime --output evals/results/runtime.json
```

The lanes intentionally prove different things:

- Quality combines five controlled component cases with one public provider replay
  through ingest, canonicalization, evidence resolution, calibration, formatting,
  and final validation. Empty denominators are `null`/N/A and cannot pass.
- Security combines twelve controlled attack cases with a real clean/injected
  public PDF pair under the local provider. Tool-call activity is N/A because this
  lane has no instrumented provider runner; it is not reported as zero.
- Runtime executes ten hash-distinct public PDFs plus a forced failure-isolation
  scenario. Its scope is local synthetic execution and excludes hosted inference
  and network latency.

Run the complete deterministic gate:

```powershell
uv run python -m pytest -q -p no:cacheprovider
uv run ruff check --no-cache src tests report
uv run basedpyright
git diff --check
```

The optional real-provider smoke uses saved Codex authentication, public synthetic
input, no event API, and no submission:

```powershell
$env:RUN_CODEX_EXEC_SMOKE = '1'
uv run python -m pytest -q -p no:cacheprovider `
  tests/integration/test_live_provider.py `
  -k real_two_paper_codex_exec_provider_smoke
Remove-Item Env:RUN_CODEX_EXEC_SMOKE
```

A passing two-request smoke must not be extrapolated to ten-paper runtime or review
quality.

## Live boundary

The `live` command is a mutating event workflow: it fetches assignments, downloads
papers, submits validated reviews, and checks receipts. It is not a local demo. It
requires an explicitly authorized `RALPHTHON_SETUP_TOKEN` and defaults to the
documented event URL.

The live path uses `codex-exec`, permits at most one retry for a typed transient
provider failure, and never replaces a failed provider review with the local
heuristic. `--provider local-heuristic` is rejected before event access. Each paper
ends in either a verified receipt or a sanitized typed failure record.

## Security boundary

Only orchestration owns paper identity, rubric, deadlines, routing, credentials,
and submission authority. Paper text, metadata, links, annotations, attachments,
and embedded instructions are evidence only and are never executed.

Reviewer calls receive sanitized evidence, trusted prompts, the rubric, and a
closed output schema. They receive no shell, secrets, arbitrary network, event API,
or cross-paper mutation capability. Detection does not automatically lower paper
scores; confidence changes only when sanitization reduces scientific evidence.

## Artifacts and report

For a single review under `runs/qa/single`, the explicit `--output` file is the
public result and `runs/qa/single/<paper_id>/` contains the audit trace:

```text
assignment.json              trusted metadata
pdf_hash.json                hash and page count, not PDF bytes
security_scan.json
parsed_structure.json
claim_ledger.json
reviewer_outputs.json
normalized_findings.json
rejected_findings.json
score_trace.json
comment_trace.json
review.json                  validated internal result
events.jsonl
```

JSON stages have SHA-256 manifests; `events.jsonl` is append-oriented. Batch runs
also write `<paper_id>/final_review.json`, `completions.jsonl`, and `summary.json`.
Secret-like values are redacted from persisted traces.

The report builder accepts only strict evaluator JSON and renders at most four
pages:

```powershell
uv run python report/build_report.py --metrics-dir evals/results `
  --output output/pdf/reviewharness-report.pdf
```

The current provider-replay trace is saved under
`evals/results/quality-conformance/`. Verbose runtime traces are reproducible and
ignored; `runtime.json` is the compact canonical runtime result.

## Known limitations

- PyMuPDF has no OCR or image/QR semantic analysis; image-only content can reduce
  evidence coverage.
- The public provider replay proves contract conformance, not open-ended hosted
  review quality.
- External novelty and unchecked technical details remain uncertain without an
  independent literature source.
- Real-provider full-mode latency and ten-paper throughput are unmeasured.
- Current public artifacts do not reproduce authenticated event envelopes, API
  limits, idempotency behavior, or receipts.
- Human correlation remains N/A until independent labels exist.

Keep real assignments, private PDFs, credentials, run directories, submissions,
and receipts out of Git. The intended private locations are ignored.

## Repository map

- `src/reviewharness/kernel.py`, `kernel_support.py`: orchestration and sanitized
  provider evidence
- `provider_contracts.py`, `claims.py`, `evidence.py`: exact provider-candidate
  canonicalization and evidence resolution
- `reviewers.py`, `codex_provider.py`, `local_provider.py`: full/fast reviewer and
  calibrator contracts
- `scoring.py`, `formatter.py`, `validation.py`: rubric calibration, inclusion
  trace, and fail-closed final gates
- `secure_ingest.py`, `parser.py`, `injection.py`: untrusted PDF ingestion
- `runner.py`, `deadline.py`, `artifacts.py`: batch scheduling and isolated traces
- `live.py`, `live_cli.py`, `api_adapter.py`: fail-closed event boundary
- `eval_quality.py`, `eval_security.py`, `eval_runtime.py`: scoped public evaluators
- `tests/fixtures/`: generated public clean, adversarial, conformance, and runtime
  inputs
- `report/`: strict artifact-derived four-page report
- `.agent/RALPHTHON_EXECPLAN.md`: living improvement and acceptance plan
- `PROGRESS.md`: current measured status and proof gaps
