# ReviewHarness

ReviewHarness is a Track 2 ICML-style review kernel for Ralphthon. It treats submitted papers as untrusted evidence, generates evidence-grounded review findings, resolves reviewer disagreement without simple majority voting, and maps verified findings to the official ICML review scales.

## Project status

This repository is initially a Codex execution specification. The implementation is created during the 12:30-15:30 hands-off Ralph Loop by following `AGENTS.md`, `.agent/PLANS.md`, `.agent/RALPHTHON_EXECPLAN.md`, and `RALPH_GOAL.md`.

## Core contract

    trusted assignment metadata + paper PDF
        -> validated ICML review JSON

Required output fields are Soundness, Presentation, Significance, Originality, Overall Recommendation, Confidence, and a constructive Comment.

## Key constraints

- Ten papers must be handled within the 16:35-17:00 production window.
- Human judge labels and private heuristics are unavailable during development.
- Prompt injection embedded in papers must not control scoring or submission.
- Every retained major factual concern requires paper-local evidence.
- No frontend, database, cloud deployment, or browser workflow is part of the core project.

## Start here

Read `docs/CODEX_PREFLIGHT.md`, then launch Codex from the repository root and verify that it loaded `AGENTS.md`. At 12:30, set the persistent goal using the content of `RALPH_GOAL.md`.

Implementation and run commands will be added by Codex and must be verified before the report is submitted.

## Documentation map

- `AGENTS.md`: durable repository rules
- `.agent/PLANS.md`: ExecPlan operating standard
- `.agent/RALPHTHON_EXECPLAN.md`: complete implementation and event plan
- `RALPH_GOAL.md`: concise persistent goal for the hands-off run
- `PROGRESS.md`: current verified state and next action
- `docs/EVENT_CONTEXT.md`: event facts and unknowns
- `docs/REVIEW_SPEC.md`: scientific review behavior
- `docs/SECURITY_THREAT_MODEL.md`: prompt-injection boundary
- `docs/EVALUATION_SPEC.md`: proxy and post-hoc evaluation
- `docs/REPORT_SPEC.md`: four-page report contract
- `rubrics/icml_review.yaml`: official score anchors
- `schemas/`: strict internal and final schemas
