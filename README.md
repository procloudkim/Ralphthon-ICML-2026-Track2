# ReviewHarness

ReviewHarness is a local, API-native ICML review kernel for Ralphthon Track 2. It converts a trusted paper identifier and local PDF into a strict, evidence-grounded review while treating all PDF-derived content as untrusted data.

Status: **P0 LOCALLY COMPLETE; LIVE EVENT/HOSTED PROVIDER UNVERIFIED**

```text
trusted assignment metadata + local paper PDF
    -> secure ingest and paper-local evidence
    -> full or fast review
    -> validated ICML review JSON
```

Human judge labels and private heuristics were unavailable during development. Human correlation is therefore **N/A**, not an estimated or synthetic score. Readiness and numeric results must be taken from freshly generated evaluator artifacts, not from this README.

## Three key contributions

1. **Evidence-weighted disagreement resolution.** Agreement informs confidence; verified evidence and central-claim impact determine priority. Supported minority findings survive, while unsupported factual criticism is rejected.
2. **Injection-resilient review boundary.** Paper content is untrusted evidence. Reviewer calls have no dangerous tools or credentials, suspicious instructions are isolated from scoring, and the final result passes sink validation without automatically penalizing scientific merit.
3. **Deadline-aware parallel review.** Bounded concurrency, paper-local state, full mode, fast fallback, and failure isolation target ten reviews within the twenty-five-minute production window.

## Architecture summary

The pipeline is `trusted assignment -> secure PDF ingest -> page-aware evidence -> claim ledger -> independent reviewer perspectives -> evidence gate -> disagreement resolution -> rubric calibration -> constructive comment -> schema and security validation`. Full mode runs method, evidence, and impact perspectives independently; fast mode uses one tri-lens pass behind the same deterministic gates.

## Security model

Only orchestration owns the trusted paper ID, rubric, deadlines, routing, and submission boundary. PDF text, metadata, links, annotations, and attachments are evidence only and are never executed. Critical and major factual concerns require paper-local support; suspicious instructions are quarantined; raw attack text does not enter calibration; and final validation rejects marker leakage, capability requests, identifier replacement, unsupported retained findings, and malformed output.

## Installation

ReviewHarness requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --locked --python 3.12
uv run python -m reviewharness --help
```

The default reviewer is the deterministic offline `LocalHeuristicProvider`. It requires no model key or network access and is the implementation exercised by the local evaluators. The typed Ralphthon event adapter exists in `src/reviewharness/api_adapter.py`, but authenticated assignment download and submission have not been verified against a live event account. The public CLI reviews local PDFs; it does not fetch or submit event data.

## Single-paper command

Full mode runs three independent reviewer perspectives:

```powershell
uv run python -m reviewharness review tests/fixtures/clean/sample.pdf --paper-id SAMPLE-001 --mode full --output runs/qa/single/review.json
uv run python -m reviewharness validate runs/qa/single/review.json
```

Fast mode uses one combined review call and the same evidence, scoring, schema, and sink-security gates:

```powershell
uv run python -m reviewharness review tests/fixtures/clean/sample.pdf --paper-id SAMPLE-001 --mode fast --output runs/qa/fast/review.json
uv run python -m reviewharness validate runs/qa/fast/review.json
```

`--paper-id` is trusted control-plane input. The paper and reviewer provider cannot replace it. A valid public review contains exactly:

```text
paper_id, soundness, presentation, significance, originality,
overall_recommendation, confidence, comment
```

## Batch command

The batch manifest is strict and must contain exactly ten assignments. Relative `pdf_path` values resolve from the repository root or manifest directory. The runner uses a monotonic 1,500-second deadline, bounded paper/model-call concurrency, per-paper artifact isolation, fast fallback, and an append-only completion stream.

```powershell
uv run python -m reviewharness batch tests/fixtures/batch/assignments.json --output-dir runs/qa/batch --deadline-seconds 1500 --paper-concurrency 5 --llm-concurrency 10
```

One paper failure is handled independently; the command exits nonzero unless every requested review completes within the deadline.

## Validation and tests

These evaluators use controlled synthetic fixtures and the deterministic offline provider. Each command writes its measured JSON before returning success or failure.

```powershell
uv run python -m reviewharness eval-quality --output evals/results/quality.json
uv run python -m reviewharness eval-security --output evals/results/security.json
uv run python -m reviewharness eval-runtime --output evals/results/runtime.json
```

Quality covers evidence support, unsupported-critique rejection, minority-finding preservation, repeatability, issue stability, and score-comment consistency. Security covers trusted-ID invariance, injection/canary leakage, capability requests, benign quoted attacks, and clean-versus-injected stability. Runtime executes a measured ten-paper local workload plus a forced-failure isolation scenario. None of these fixtures are human ground truth, and none establishes live-model or human-reviewer correlation.

Run the repository checks separately:

```powershell
uv run pytest -q
uv run ruff check src tests report
uv run basedpyright
```

## Output schema

A valid public review contains exactly `paper_id`, `soundness`, `presentation`, `significance`, `originality`, `overall_recommendation`, `confidence`, and `comment`. Dimension scores are integers from 1 to 4, Overall Recommendation is 1 to 6, Confidence is 1 to 5, and the constructive comment is nonempty. `paper_id` always comes from trusted command or assignment input.

## Known limitations

- PyMuPDF has no OCR or image/QR semantic analysis, so image-only content can reduce evidence coverage.
- Injection detection is defense in depth; capability isolation and final sink validation are the primary controls.
- The deterministic offline provider proves local contracts and orchestration, not hosted-model quality or agreement with human reviewers.
- External novelty and unchecked technical details remain uncertain without external evidence.
- Authenticated event envelopes, idempotency, API limits, and submission receipts remain unverified.

## Reproducibility notes

### Anonymous report build

The report builder accepts only strict saved evaluator JSON and renders a four-page PDF. Generate all three current metric files first:

```powershell
uv run python report/build_report.py --metrics-dir evals/results --output output/pdf/reviewharness-report.pdf
```

Every numeric claim in the report is loaded from `evals/results/quality.json`, `security.json`, or `runtime.json`. Human correlation remains N/A until organizers provide real labels.

### Artifact layout

For a single review whose output directory is `runs/qa/single`, the public result is the explicit `--output` path and the audit trace is isolated below `runs/qa/single/<paper_id>/`:

```text
review.json                         public CLI output
<paper_id>/assignment.json          trusted metadata
<paper_id>/pdf_hash.json            hash and page count, not PDF bytes
<paper_id>/security_scan.json
<paper_id>/parsed_structure.json
<paper_id>/claim_ledger.json
<paper_id>/reviewer_outputs.json
<paper_id>/normalized_findings.json
<paper_id>/rejected_findings.json
<paper_id>/score_trace.json
<paper_id>/review.json               validated internal result
<paper_id>/events.jsonl
```

Each per-paper JSON stage also has a SHA-256 manifest; `events.jsonl` is the append-oriented exception. A batch additionally writes `<paper_id>/final_review.json`, root-level `completions.jsonl`, and `summary.json`. Evaluator evidence lives in `evals/results/`, and the report is written to `output/pdf/reviewharness-report.pdf`.

Secret-like values are redacted from persisted traces. Keep real assignments, private PDFs, credentials, run directories, and event submissions out of Git; the intended private locations (`runs/`, `assignments/`, `private_papers/`, and `submissions/`) are ignored. Never add a private PDF or submission merely to reproduce a local command.

### Detailed security boundaries

- Paper text, metadata, links, annotations, and attachments are evidence only. PDF actions and links are never executed.
- Reviewer calls receive sanitized page evidence, trusted rubric/prompts, and a strict output schema. They receive no shell, secrets, arbitrary tools, submission capability, or cross-paper state.
- Critical and major factual findings require paper-local locators. Unsupported factual criticism is rejected; a supported minority finding is not removed by majority vote.
- `schemas/finding.schema.json` is the canonical storage-compatibility shape, not a schema-only safety proof. Runtime resolution verifies locators against parsed blocks and maps `target_claim_id` through the claim ledger; final validation rejects unsupported retained states, retained objective or mixed critical/major findings without a verified locator or recommended check, and finding-trace mismatches.
- Detection alone does not lower scientific scores. Confidence may fall only when sanitization materially limits review evidence.
- Internal reviews use trusted `paper_id`, `overall_recommendation`, and `comment`. At the isolated event boundary, the server-owned assignment `ordinal` is used and the adapter translates `overall_recommendation -> overall` and `comment -> comments`.
- The current event response models encode the documented envelope and reject unsafe downloads or malformed receipts. Final organizer envelope changes, credentials, idempotency behavior, API limits, and authenticated live execution remain unverified until exercised against the event service.

### Repository map

- `src/reviewharness/kernel.py` and `kernel_support.py`: single-paper orchestration, sanitized evidence preparation, and artifact persistence
- `src/reviewharness/providers.py`, `local_provider.py`, and `reviewers.py`: capability-free provider contract, deterministic offline implementation, and bounded full/fast reviewer calls
- `src/reviewharness/parser.py`, `secure_ingest.py`, and `injection.py`: page-aware untrusted-PDF ingest and quarantine
- `src/reviewharness/claims.py`, `evidence.py`, `scoring.py`, `formatter.py`, and `validation.py`: claim/evidence resolution, rubric calibration, comment construction, and final sink gates
- `src/reviewharness/deadline.py`, `runner.py`, and `artifacts.py`: monotonic deadline control, bounded streaming batches, recovery, and isolated persistence
- `src/reviewharness/eval_quality.py`, `eval_security.py`, and `eval_runtime.py`: controlled quality, security, and runtime evaluators
- `src/reviewharness/cli.py` and `api_adapter.py`: public local command surface and isolated live-event wire boundary
- `prompts/`: trusted full/fast reviewer prompts
- `rubrics/icml_review.yaml`: ICML score anchors
- `schemas/`: canonical storage-compatibility and public-submission schemas; runtime gates remain authoritative for retained findings
- `tests/fixtures/`: controlled clean, adversarial, quality, batch, and report fixtures
- `report/build_report.py`: strict artifact-derived four-page PDF builder
- `.agent/RALPHTHON_EXECPLAN.md`: living implementation and acceptance plan
- `PROGRESS.md` and `EXPERIMENTS.jsonl`: verified progress and experiment ledger
