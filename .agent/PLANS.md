# Codex Execution Plans

An execution plan, called an ExecPlan in this repository, is a self-contained living document that Codex can follow to deliver a demonstrably working result over a long or complex task.

## How to use an ExecPlan

Before implementing an ExecPlan, read the entire plan and all files it explicitly names. Do not assume any prior conversation or memory. The plan must contain enough context for a contributor who only has the current working tree.

While implementing:

- proceed milestone by milestone without asking for routine next steps;
- keep every section current as work progresses;
- resolve reversible ambiguities autonomously;
- record material decisions and discoveries in the plan;
- run the validation named by each milestone;
- inspect produced artifacts, not only logs;
- commit verified checkpoints when repository policy allows;
- keep the system runnable throughout.

An ExecPlan is complete only when its observable acceptance criteria pass. Code that merely appears to satisfy the design is not sufficient.

## Required qualities

Every ExecPlan must be:

- self-contained;
- written in plain language;
- specific about files, commands, interfaces, and expected observations;
- updated as discoveries and decisions occur;
- centered on user-visible behavior;
- explicit about security boundaries, fallbacks, and failure modes;
- honest about unknowns and unmeasured claims.

Define domain-specific terms when first used. Do not refer vaguely to “the earlier discussion.” Put the relevant decision in the plan.

## Required sections

An ExecPlan must include:

1. Purpose and user-visible outcome
2. Event or business constraints
3. Known facts, unknowns, and source-of-truth precedence
4. Current repository state
5. Architecture and trust boundaries
6. Input and output contracts
7. Milestones and concrete implementation steps
8. Validation commands and expected evidence
9. Progress checklist with timestamps
10. Decision log with rationale
11. Unexpected discoveries
12. Risks, fallbacks, and recovery behavior
13. Final outcomes and remaining limitations

## Progress discipline

At each stopping point, update the plan to state:

- what was completed;
- what was actually verified;
- what remains;
- the next exact action;
- whether the run is blocked;
- the smallest human action needed when blocked.

Avoid vague entries such as “continue improving.”

## Experiment discipline

For each experiment, record:

- hypothesis;
- one changed component;
- fixtures or cases used;
- metrics before;
- metrics after;
- runtime effect;
- security effect;
- keep or revert decision.

Do not tune and evaluate on the same human-labeled cases when a held-out split is possible. Never invent missing labels or measurements.

## Proof and acceptance

Each milestone must name the smallest command or artifact that proves it works. Prefer focused tests before broad test suites. When output quality is subjective, combine deterministic checks with a stable rubric-based evaluator and manual artifact inspection during the permitted human phase.

The final plan must make it possible to resume from the repository alone.
