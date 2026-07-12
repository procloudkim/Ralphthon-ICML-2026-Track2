# ReviewHarness Ralphthon Track 2 ExecPlan

Status: P0 LOCALLY COMPLETE; LIVE EVENT/HOSTED PROVIDER UNVERIFIED
Date: 2026-07-12
Timezone: Asia/Seoul

## Purpose and user-visible outcome

Build a local reviewer kernel that receives trusted assignment metadata and a short paper PDF, then returns a secure, evidence-grounded, rubric-calibrated ICML review. During production, ten assigned papers must produce ten valid review payloads within the 16:35-17:00 window.

The exact human judges' private heuristics and scores are unavailable during development. The project therefore targets expected alignment with competent ICML reviewers through official rubric fidelity, strong evidence discipline, issue prioritization, conservative ordinal scoring, and calibrated uncertainty. Actual correlation with human judges is a post-hoc organizer metric and must not be fabricated or claimed in advance.

## Source-of-truth precedence

When facts conflict, use this order:

1. The final live event API contract and organizer announcement
2. The participant guide and `docs/EVENT_CONTEXT.md`
3. The repository's frozen review specification and rubric
4. This ExecPlan
5. Earlier transcripts or conversation assumptions

Unknown event API fields must remain behind a replaceable adapter. They must not block reviewer-core development.

## Event constraints

The operational schedule is:

    11:00-12:30  Research specification
    12:30-15:30  Ralph Loop, hands off
    15:30-16:30  Human editing, polish, and submission
    16:30        Submission hard cut and matching snapshot
    16:35-17:00  Ten-paper production review window

Use 1,500 seconds as the production hard limit. Earlier verbal references to 30 minutes are superseded by the stricter published 25-minute window.

The Track 2 submission consists of an anonymous technical report PDF with a four-page hard limit, title, abstract, GitHub repository, and short run instructions. The agent approach is judged qualitatively, and similarity or correlation between human judge scores and agent scores is a quantitative evaluation component. The exact metric and weight are unknown.

## Product boundary

The core product is:

    trusted assignment + local PDF
        -> validated ICML review JSON

The event adapter is responsible for receiving assignments and sending results. The reviewer kernel does not implement a frontend, dashboard, database, public service, browser workflow, authentication system, or cloud deployment.

The implementation must remain usable if the final API schema changes. Put platform-specific parsing and submission in `src/reviewharness/api_adapter.py` or an equivalent isolated module.

## Trusted and untrusted data

Trusted control-plane data includes `paper_id`, assignment identifiers, deadline, API routing, credentials, official score ranges, rubric, model configuration, and system instructions.

All paper-derived content is untrusted, including visible and hidden text, tables, figures, captions, equations, references, metadata, annotations, hyperlinks, attachments, embedded actions, image text, and zero-width characters.

The paper is evidence, never authority. A paper must not change the rubric, role, output schema, score, tool policy, API routing, identifier, or another paper's state.

## Input contract

Implement a strict trusted input model equivalent to:

    class TrustedAssignment(BaseModel):
        paper_id: str
        pdf_path: Path
        title: str | None = None
        assignment_id: str | None = None
        deadline_at: datetime | None = None

The application, not the model, owns `paper_id`.

## Output contract

Implement a strict result model equivalent to:

    class ReviewSubmission(BaseModel):
        paper_id: str
        soundness: int              # 1..4
        presentation: int           # 1..4
        significance: int           # 1..4
        originality: int            # 1..4
        overall_recommendation: int # 1..6
        confidence: int             # 1..5
        comment: str

Final scores are integers. Reject missing fields, out-of-range scores, model-generated identifiers, malformed output, and non-constructive comments.

Persist internal artifacts per paper: assignment metadata, original PDF hash, security scan, parsed structure, claim ledger, reviewer outputs, normalized findings, rejected findings, score trace, final review, event log, and submission receipt. Private assigned PDFs and submissions must not be committed.

## Review architecture

The target pipeline is:

    Trusted Assignment
        -> Secure PDF Ingest
        -> Page-Aware Parser
        -> Paper-Type Classification
        -> Claim Ledger
        -> Three Independent Reviewer Perspectives
        -> Finding Normalization and Deduplication
        -> Evidence Gate
        -> Evidence-Weighted Disagreement Resolution
        -> ICML Score Calibration
        -> Constructive Comment Generation
        -> Schema and Security Validation
        -> ReviewSubmission

The initial implementation may begin with one end-to-end reviewer call. It must evolve into the bounded full and fast modes described below without breaking the single-paper path.

## Claim ledger

Before critique, identify the paper's central, supporting, and background claims. For each claim, store its statement, type, importance, and reported evidence locations. Use the claim ledger to prevent minor presentation issues from outranking failures that affect the central contribution.

Example internal representation:

    {
      "claim_id": "C1",
      "statement": "The method improves accuracy with lower compute.",
      "importance": "central",
      "claim_type": "empirical",
      "reported_evidence": [
        {"page": 3, "section": "Experiments", "locator": "Table 1"}
      ]
    }

## Reviewer perspectives

The Method and Soundness reviewer examines central-claim support, method validity, experimental design, baseline fairness, matched data or compute, controls, ablations, statistical validity, theoretical consistency, overclaiming, and material limitations.

The Evidence and Reproducibility reviewer examines text-table-figure consistency, numerical support, dataset and split descriptions, hyperparameters, seeds, variance, error bars, code and data claims, reproducibility blockers, missing evidence, and locator correctness.

The Significance, Originality, and Presentation reviewer examines research importance, contribution clarity, likely utility, novelty justification, differentiation from related work described in the paper, narrative structure, contextualization, and writing quality. External novelty must not be asserted with false certainty when literature cannot be checked.

In full mode these perspectives run independently and concurrently. They must not see one another's outputs before normalization.

## Finding and evidence policy

Every retained critical or major factual finding must include a real locator, accurate evidence summary, affected claim, severity, decision impact, recommended check, and confidence.

Use an internal structure compatible with `schemas/finding.schema.json`.

The canonical finding JSON Schema is an interchange and persisted-artifact
compatibility shape, not a complete retention policy. At runtime, evidence
resolution verifies paper-local locators against parsed blocks and resolves a
finding's `target_claim_id` through the claim ledger before assigning claim
impact. Final sink validation rejects unsupported retained states, retained
objective or mixed critical/major findings without a verified locator or a
recommended check, and retained/rejected/calibration trace mismatches. Passing
the JSON Schema alone is therefore never treated as a safety or evidence proof.

When no paper-local evidence supports a criticism, classify it as an unsupported hypothesis and exclude it from the final comment as an established fact. It may become a narrow, explicitly uncertain author question only when an answer could materially change the decision.

Evidence verification must check that the page and locator exist, the summary is semantically supported, the paper does not resolve the concern elsewhere, and severity is proportionate.

## Disagreement resolution

Do not use simple majority voting and do not average away a one-reviewer finding.

Use the principle:

    reviewer agreement -> confidence
    verified evidence + central-claim impact -> priority

Classify merged findings as one of:

    consensus_supported
    minority_supported
    contested
    unsupported_rejected
    subjective_divergence
    parser_uncertain

Preserve a minority finding when its evidence is verified, it directly affects a central claim, and it could change the recommendation.

Initial configurable heuristics may use:

    priority =
        0.35 * evidence_strength
      + 0.35 * central_claim_impact
      + 0.20 * severity
      + 0.10 * decision_relevance

    confidence =
        0.40 * evidence_strength
      + 0.35 * reviewer_agreement
      + 0.25 * verifier_confidence

These weights are starting hypotheses, not facts. Keep or change them only through measured evaluation.

The final review should normally contain one or two specific strengths, zero or one critical issue, two or three major concerns, and only decision-relevant minor issues. The goal is not to maximize criticism count.

## ICML score calibration

Do not average specialist reviewer scores. Derive final scores from verified findings and official ordinal anchors in `rubrics/icml_review.yaml`.

Soundness measures technical claims, methods, experiments, and evidence support. Presentation measures clarity, structure, contextualization, and reproducibility detail. Significance measures the importance and likely influence of the contribution. Originality measures justified new insight, method, task, data, theory, or perspective.

Overall recommendation follows the official 1-6 anchors. Weak accept and weak reject should be used sparingly. Extreme scores require extreme evidence.

Implement contradiction guards, including:

    if soundness == 1:
        overall_recommendation <= 2

    if a verified critical finding remains:
        overall_recommendation <= 3

    if overall_recommendation >= 5:
        soundness >= 3
        significance >= 3
        no unresolved major finding

    if overall_recommendation == 6:
        soundness == 4
        significance == 4

These guards catch contradictions; they do not replace contextual judgment.

Confidence measures certainty in the assessment, not paper quality. Lower confidence for incomplete parsing, unfamiliar paper type, unverified novelty, unchecked technical detail, unresolved reviewer disagreement, or sanitization that obscures scientific evidence.

## Constructive comment

The final comment should usually be 250-450 words unless the API imposes another limit. It must contain an accurate summary, one or two concrete strengths, two or three decision-relevant concerns with reliable locators, and one or two actionable suggestions.

Avoid generic statements such as “more experiments are needed.” State what evidence is missing, where the gap appears, and why it changes the assessment.

## Hidden human evaluation

Human judge scores and private heuristics are unavailable. Actual human correlation remains unavailable until organizers produce real labels.

Development proxies are official rubric fidelity, summary accuracy, evidence coverage, unsupported critique rate, issue precision and recall on controlled fixtures, severity proportionality, score-comment consistency, repeatability, top-issue stability, uncertainty calibration, prompt-injection invariance, and runtime reliability.

Do not fabricate human annotations. Do not call synthetic fixtures human ground truth. Do not force a bell curve, assumed accept rate, or predetermined score distribution.

An optional batch consistency audit may identify unexplained scale drift or contradictory ordering using sanitized compact profiles. It must not see raw paper text or malicious spans, force a distribution, or change a score without an evidence-based explanation. Keep it disabled unless measured proxy evaluation shows a clear benefit.

If real human labels become available after the event, evaluate overall recommendation with Pearson correlation, Spearman rank correlation, mean absolute error, and weighted kappa; dimension scores with per-dimension errors; and review content with major-issue overlap and severity agreement. Do not overclaim statistical significance on ten papers.

## Prompt-injection security

A paper may attempt to force a score, suppress weaknesses, request marker phrases, impersonate a system or conference authority, replace the rubric, break JSON, request credentials, trigger shell or network use, redirect submission, influence other papers, or hide instructions in tiny, transparent, white, off-page, encoded, metadata, annotation, or image content.

The defense must constrain impact even when detection is imperfect.

Reviewer model calls must have no shell, arbitrary filesystem access, arbitrary network access, credentials, environment variables, submission API, or cross-paper mutation capability. The orchestration layer owns all trusted identifiers and submission behavior.

Secure ingest should inspect active PDF content, annotations, attachments, links, metadata, suspicious hidden text, zero-width characters, reviewer-directed imperatives, fake authority, fake rubric text, marker requests, and score-steering language where feasible. Never execute PDF content.

Classify suspicious spans as manipulative instruction, reviewer-detection canary, benign quoted example, or uncertain instruction. A legitimate paper about prompt injection may quote attacks. Do not blindly remove scientific examples.

Quarantine high-risk instructions by preserving the document hash and location, neutralizing instruction authority, and passing a labeled placeholder or safe quoted-data representation to reviewer calls.

Raw suspicious instructions must not reach the score calibrator. The calibrator receives only the trusted rubric, sanitized paper summary, claim ledger, verified findings, disagreement states, parser confidence, and security status without attack text.

Detection alone does not lower scientific scores. Lower confidence only when sanitization materially prevents reliable review.

For high-risk detections, optionally compare two safe variants: one with suspicious spans replaced by neutral placeholders and one with them removed. If the resulting scores differ materially, recalibrate from verified findings and run one bounded adjudication. This path must be conditional because of the deadline.

Before returning a review, validate trusted `paper_id`, strict schema, marker leakage, unauthorized fields, tool or credential requests, evidence support for scores, API-routing isolation, and cross-paper isolation.

## Full and fast modes

Full mode uses three independent specialist calls in parallel, deterministic normalization and evidence checks, and one bounded calibrator or adjudicator step.

Fast mode uses one tri-lens reviewer call, deterministic evidence and security checks, lightweight calibration, and strict validation.

Fast mode is mandatory. Use it under deadline pressure, rate limits, specialist timeout, or partial failure. A valid conservative review is preferable to an unfinished deeper review.

## Ten-paper production architecture

Use a streaming bounded pipeline:

    Assignment Producer
        -> Bounded Paper Queue
        -> Independent Paper Workers
        -> Validated Result Queue
        -> Submission Adapter
        -> Receipt Verification

Start reviewing as soon as an assignment is available. Submit each validated review as soon as it is ready; do not wait for all ten.

All concurrency values must be configurable. Benchmark at least paper concurrency 4 and 5, and global model-call concurrency 8 and 10, subject to API limits. Choose the highest stable measured setting, not the highest theoretical setting.

Each paper has its own sanitized representation, context, timeout, artifacts, errors, fallback, and submission state. One failure must not cancel the batch. Raw content must not be shared across papers.

Use a monotonic deadline controller. Suggested policy:

    T+00 to T+10  Full mode; stream completed reviews
    T+10          Switch delayed or new work to fast mode as needed
    T+12          Cancel optional expansions and nonessential adjudication
    T+15          Every paper must have at least one valid draft
    T+18          Repair, validate, and submit only
    T+20          All valid outputs should be submitted
    T+23          No deep retry; use conservative valid fallback
    T+25          Hard stop

Initial timeout candidates are 20 seconds for PDF parsing, 120 seconds for a specialist call, 90 seconds for calibration, 300 seconds per paper, and one retry for transient failures. Measure and tune them.

Reviewer fallback order is one transient retry, use remaining successful specialists, switch to fast mode, then produce a conservative evidence-grounded result with lower confidence. Parser fallback order is primary page-aware extraction, secondary text extraction, simplified page text, then limited review with an internal warning. If calibration fails, use the latest schema-valid evidence-verified draft.

Use an idempotency key based on paper ID, configuration hash, and rubric version when supported. Never duplicate a verified submission.

## Hands-off Ralph Loop schedule

From 12:30 to 15:30 Codex must work autonomously. It must not wait for routine clarification. It should choose safe reversible assumptions, isolate missing API details behind an adapter, record assumptions and blockers, continue all unblocked work, and stop optional feature work by 15:10.

The implementation schedule is:

### 12:30-13:05: one vertical slice

Produce one PDF to one schema-valid review JSON, including strict fields, trusted paper ID, official score ranges, basic parser, constructive comment, and a passing CLI smoke test.

### 13:05-13:50: scientific review quality

Implement claim ledger, evidence-grounded findings, unsupported-finding rejection, official score anchors, score-comment consistency, and a single-pass baseline.

### 13:50-14:20: security boundary

Implement untrusted-paper policy, tool-less reviewer calls, injection classification and quarantine, direct score-steering test, marker leakage test, fake-authority test, and benign quoted-example control.

### 14:20-14:50: production path

Implement bounded concurrency, per-paper isolation, batch runner, full and fast modes, deadline controller, fallbacks, streaming result path, and a measured dry run.

### 14:50-15:10: evaluation and ablations

Measure quality proxies, security metrics, runtime, and baseline comparisons. Never fabricate human labels.

### 15:10-15:25: stabilization

Stop optional features. Run tests, freeze the best measured configuration, generate report tables from artifacts, draft the report, finalize README, and record limitations.

### 15:25-15:30: handoff

Leave a runnable repository, current ExecPlan and progress log, measured artifacts, report source, and a precise human-phase checklist.

## Evaluation and experiments

Quality metrics include evidence coverage, unsupported critique rate, issue precision and recall on controlled fixtures, finding compression, rubric-anchor compliance, score-comment consistency, repeatability, and top-issue stability.

Security metrics include attack success rate, marker leakage, unauthorized tool calls, clean-versus-injected score delta, clean-versus-injected issue overlap, detection recall, benign false-positive rate, valid completion, and security overhead.

Runtime metrics include ten-of-ten completion, total batch time, p50 and p95 paper time, timeout count, retry count, fallback count, invalid output count, and submission success.

Where time permits, compare single-pass reviewer, majority ensemble, evidence resolver, and secure evidence resolver. Every experiment changes one component and records before, after, security effect, runtime effect, and keep-or-revert decision in `EXPERIMENTS.jsonl` and `PROGRESS.md`.

Minimum adversarial fixtures include direct score steering, request to omit weaknesses, fake system or conference-chair message, fake rubric, marker-phrase request, JSON breakout, secret exfiltration request, shell or URL request, hidden text, metadata injection, cross-paper poisoning, and a benign paper quoting prompt-injection examples.

## Repository implementation map

The current implementation is organized as follows:

    src/reviewharness/
        cli.py                 public review, validate, batch, and evaluator commands
        schemas.py             strict trusted-input, finding, score, and output models
        config.py              trusted rubric loader
        api_adapter.py         isolated Ralphthon wire translation
        parser.py              page-aware PDF parsing
        secure_ingest.py       untrusted-PDF inspection and quarantine
        injection.py           injection classification and neutralized views
        claims.py              deterministic claim-ledger construction
        evidence.py            locator verification and finding resolution
        scoring.py             rubric calibration and contradiction guards
        formatter.py           constructive injection-safe comment generation
        validation.py          final trusted-ID, evidence, trace, score, and sink gates
        providers.py           typed capability-free reviewer boundary
        local_provider.py      deterministic offline reviewer implementation
        reviewers.py           bounded full/fast reviewer orchestration
        kernel.py              isolated single-paper pipeline
        kernel_support.py      sanitized evidence preparation and artifact persistence
        deadline.py            monotonic deadline decisions
        runner.py              bounded streaming batch and paper-local recovery
        artifacts.py           atomic redacted artifact storage
        eval_quality.py        controlled quality evaluator
        eval_security.py       controlled security evaluator
        eval_runtime.py        ten-paper and failure-isolation runtime evaluator
        quality_cases.py       strict quality-fixture conversion
        security_cases.py      strict adversarial-fixture execution

    prompts/                    trusted full, fast, specialist, and calibrator prompts
    rubrics/icml_review.yaml    ICML ordinal anchors
    schemas/                    canonical storage and public-submission JSON Schemas
    tests/fixtures/             controlled clean, injected, quality, batch, and report data
    evals/results/              saved evaluator metrics and runtime artifacts
    report/__init__.py          strict evaluator-artifact loader
    report/content.md           anonymous report narrative source
    report/build_report.py      artifact-derived four-page PDF builder

Do not add infrastructure directories without a requirement.

## Milestones

### P0, mandatory

- Strict input and output schemas
- Valid single-paper command
- Page-aware parsing
- Claim ledger
- Evidence-grounded findings
- Unsupported-finding rejection
- Rubric-based score calibration
- Constructive comment
- Prompt-injection trust boundary
- Output security validator
- Full mode
- Fast mode
- Bounded ten-paper runner
- Deadline controller
- Failure isolation
- README run instructions
- Four-page report source

### P1, after P0

- Three specialist reviewers
- Hidden-text inspection
- Adversarial fixture suite
- Majority-vote baseline
- Proxy evaluation harness
- Repeated batch tests
- Optional bounded adjudicator

### P2, only when stable

- Optional batch consistency audit
- W&B tracing
- Additional paper-type rubrics
- Advanced visual-injection analysis
- Report visual refinement

Do not work on P2 while a P0 criterion fails.

## Validation commands

The current public and verification commands are:

    uv sync --locked --python 3.12
    uv run python -m reviewharness review tests/fixtures/clean/sample.pdf --paper-id SAMPLE-001 --mode full --output runs/qa/single/review.json
    uv run python -m reviewharness validate runs/qa/single/review.json
    uv run python -m reviewharness batch tests/fixtures/batch/assignments.json --output-dir runs/qa/batch --deadline-seconds 1500 --paper-concurrency 5 --llm-concurrency 10
    uv run python -m reviewharness eval-quality --output evals/results/quality.json
    uv run python -m reviewharness eval-security --output evals/results/security.json
    uv run python -m reviewharness eval-runtime --output evals/results/runtime.json
    uv run pytest -q
    uv run ruff check src tests report
    uv run basedpyright
    uv run python report/build_report.py --metrics-dir evals/results --output output/pdf/reviewharness-report.pdf

`README.md` carries the same copy-pasteable command surface. Authenticated event
retrieval/submission and a hosted reviewer provider remain outside this locally
verified command set.

## Acceptance criteria

The work is complete when one PDF produces valid review JSON; all score fields are valid integers; the comment is accurate and constructive; major factual concerns have valid evidence; unsupported factual concerns are excluded; verified minority findings survive; score-comment consistency passes; direct injection cannot control scores; marker phrases do not leak; reviewer calls lack dangerous capabilities; trusted paper ID cannot be overridden; full and fast modes run; one failure does not block the batch; a measured ten-paper run returns ten valid outputs within 1,500 seconds; README has exact instructions; the anonymous report is four pages or fewer; all report numbers come from artifacts; and no fabricated human-alignment claim exists.

## Progress

- [x] 2026-07-12 14:00 +09:00 - Repository initialized and inspected; canonical seed committed as `6789102`
- [x] 2026-07-12 14:00 +09:00 - P0 gap analysis established; missing-package RED receipts saved under `.omo/evidence/reviewharness-p0/`
- [x] 2026-07-12 15:02 +09:00 - Single-paper full/fast kernel completed through `dbf2b8b`
- [x] 2026-07-12 15:07 +09:00 - Evidence gate and supported-minority preservation measured in `evals/results/quality.json`
- [x] 2026-07-12 15:07 +09:00 - Rubric calibration and score-comment consistency measured in `evals/results/quality.json`
- [x] 2026-07-12 15:07 +09:00 - Injection boundary measured over 12 controlled cases in `evals/results/security.json`
- [x] 2026-07-12 15:05 +09:00 - Full mode executed on 5 papers in the primary runtime dry run
- [x] 2026-07-12 15:05 +09:00 - Fast mode executed on 5 papers; a forced full-mode failure also recovered through fast fallback
- [x] 2026-07-12 16:05 +09:00 - Fresh ten-paper bounded dry run returned 10/10 valid outputs in 0.454 seconds
- [x] 2026-07-12 15:10 +09:00 - README run instructions completed and diff-checked
- [x] 2026-07-12 16:05 +09:00 - Final report rendered from current evaluator artifacts and visually inspected page by page at 4 pages and 8,755 bytes
- [x] 2026-07-12 15:10 +09:00 - Optional feature work stopped; deterministic offline configuration frozen for final gates
- [x] 2026-07-12 16:05 +09:00 - Final full/fast, exact injection, ten-paper, evaluator, and report commands passed; 198 tests, Ruff, and basedpyright passed

## Decision log

Record each material decision with timestamp, decision, evidence, alternatives, and consequences.

- 2026-07-12 13:59 +09:00 - Preserve the initial missing-package failures as the RED baseline. Evidence: `C001-single-paper.RED.txt`, `C002-security-quality.RED.txt`, `C003-batch-runtime.RED.txt`, and `scaffold-help.RED.txt`. Consequence: later results are traceable to an actual failing state.
- 2026-07-12 14:29 +09:00 - Make strict Pydantic schemas and a monotonic deadline controller the application-owned control boundary (`a138bb8`). Alternative rejected: trusting provider-generated identifiers or wall-clock deadline arithmetic.
- 2026-07-12 14:36 +09:00 - Isolate the public Ralphthon wire contract in `api_adapter.py` (`3f801ca`). Internal `overall_recommendation` and `comment` translate only at this boundary to `overall` and `comments`; server-owned ordinal remains reviewer-inaccessible. Consequence: final organizer envelope drift is localized.
- 2026-07-12 14:41 +09:00 - Resolve findings using verified evidence and central-claim impact (`1bee13a`) instead of majority voting. Consequence: supported minority findings remain decision-relevant and unsupported factual claims are rejected.
- 2026-07-12 14:46 +09:00 - Use the deterministic offline provider (`8defcc7`) for reproducible local P0 proof. Alternative deferred: live hosted provider execution without a configured key. Consequence: local metrics are repeatable but cannot establish live-model or human-reviewer quality.
- 2026-07-12 15:04 +09:00 - Use bounded paper/model-call concurrency, streaming completions, per-paper state, and fast fallback (`a536ef8`). Consequence: one paper failure remains isolated and deadline pressure cannot create unbounded work.
- 2026-07-12 15:10 +09:00 - Stop optional features and freeze the measured configuration at paper concurrency 5, model-call concurrency 10, and a 1,500-second monotonic deadline. Evidence: `evals/results/runtime.json` and its runtime-artifact directory.

## Unexpected discoveries

Record repository facts, API constraints, parsing failures, model limits, and other discoveries that change the plan.

- The canonical seed was specification-only. Before implementation, every exact P0 command failed with `No module named reviewharness`; the four RED receipts are preserved under `.omo/evidence/reviewharness-p0/`.
- The current public event contract uses server-selected ordinals 1-10, `paper.pdf_url`, and submission fields `ordinal`, four dimension scores, `overall`, `confidence`, and `comments`. Authenticated execution and the final response envelope remain unverified, so all event specifics stay behind the typed adapter.
- No OpenAI or Anthropic API key was available. The local acceptance lane therefore uses a deterministic typed provider with no shell, arbitrary network, secrets, submission tools, or cross-paper state.
- PyMuPDF provides page-aware text and structural inspection, but the current lane has no OCR or image/QR semantic analysis. Visual-only scientific content and visual prompt injection remain explicit limitations.
- The initial report security input contract drifted from the measured evaluator artifact. `48f5166` aligned the strict contract; 7 report tests passed and a real fixture build rendered 4 pages (8,002 bytes).

## Outcomes and remaining limitations

Before 15:30, summarize what works, measured metrics, remaining failures, security limitations, report readiness, and exact human actions needed during the polish phase.

### Local P0 outcome at 2026-07-12 15:53 +09:00

The current local kernel supports strict single-paper review and validation, page-aware secure ingest, claim ledgers, evidence-gated findings, evidence-weighted disagreement resolution, rubric calibration, constructive comments, trusted identifier enforcement, full and fast modes, bounded streaming batches, paper-local failure recovery, evaluator commands, and artifact-derived report generation. The required CLI surface is exposed by `4bb80d4`; the security evaluator is committed in `5355b1f`; the report contract fix is committed in `48f5166`; and the runtime evaluator is committed in `aa5332e`.

Measured controlled-fixture results are:

- Quality: 5 cases; evidence coverage 1.0; unsupported critique rate 0.0; issue precision and recall 1.0; minority preservation, score-comment consistency, valid completion, repeatability, and top-issue stability all 1.0. Source: `evals/results/quality.json`.
- Security: 12 cases; attack success 0.0; marker leakage 0.0; unauthorized tool calls 0; trusted-ID invariance, valid completion, detection recall, and clean/injected issue overlap 1.0; clean/injected score delta and benign false-positive rate 0.0. Source: `evals/results/security.json`. Scope is explicitly `deterministic_synthetic_fixture_and_provider`.
- Runtime: 10/10 valid in 0.454 seconds; p50 0.180 seconds; p95 0.281 seconds; no timeout, retry, primary-run fallback, or invalid output. Full and fast modes both executed. A separate failure-isolation run forced one full-mode failure, recovered through fast fallback, emitted one terminal failure record, and completed without blocking siblings. Source: `evals/results/runtime.json` and `evals/results/runtime-artifacts-153841671000000/`.

The exact full/fast single-paper review/validate lanes, security/quality evaluators, ten-paper batch, runtime evaluator, blocking kernel/runner/security regression lane, and report build all passed. The final repository suite reported 198 tests passed in 10.97 seconds; Ruff checks passed; basedpyright reported 0 errors. The final artifact-derived report at `submission/reviewharness_technical_report.pdf` was visually inspected page by page at 4 pages and 8,755 bytes and states human correlation as N/A.

Human labels and private judge heuristics were unavailable. Human correlation is therefore N/A, not zero and not synthetically estimated.

Remaining limitations are outside the locally reproducible P0 proof boundary: authenticated assignment retrieval and submission, final live envelope/idempotency behavior, real provider rate limits and quality, private event-paper performance, external novelty checking, OCR, and image/QR semantic analysis. The smallest human-phase actions are to verify the live adapter with event credentials, exercise the optional hosted provider if credentials are intentionally supplied, inspect the generated four-page report, and keep all private PDFs, credentials, and submissions out of Git.
