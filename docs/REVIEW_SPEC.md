# Scientific Review Specification

## Purpose

Produce a concise ICML-style review comparable in structure and discipline to a competent human reviewer, while acknowledging that human scientific judgment is heuristic and only partially reducible to explicit rules.

The official run is autonomous. Human governance occurs before the run through the rubric, evidence requirements, severity anchors, and score anchors.

## Supported scope

Optimize first for short empirical machine-learning papers, including method, analysis, benchmark, and dataset papers with substantial empirical evaluation. For theory, systems, position, or unfamiliar papers, apply the base rubric, avoid irrelevant empirical demands, and lower Confidence when details cannot be verified.

## Review perspectives

### Method and Soundness

Check central-claim support, method validity, experimental design, baseline fairness, controls, ablations, statistics, overclaiming, and material limitations.

### Evidence and Reproducibility

Check text/table/figure consistency, numerical support, data and split descriptions, hyperparameters, seeds, variance, code/data claims, reproducibility blockers, and evidence locators.

### Significance, Originality, and Presentation

Check research importance, contribution clarity, likely utility, novelty justification, contextualization, narrative structure, and writing quality. Do not claim external novelty with false certainty.

## Claim-first review

Extract central, supporting, and background claims before criticism. Link every major finding to an affected claim when possible. Central-claim impact is a primary priority signal.

## Evidence policy

Every retained critical or major factual concern must contain a real page and a reliable section, table, figure, equation, or text locator when available; an accurate evidence summary; impact on the paper; and a recommended author check.

A criticism without paper-local evidence is an unsupported hypothesis. Exclude it as a fact. Convert it into an explicitly uncertain question only when an answer could materially change the assessment.

## Disagreement policy

Do not use simple majority voting.

- Agreement informs confidence.
- Evidence strength, central-claim impact, severity, and decision relevance inform priority.
- A verified minority finding is preserved.
- A contested finding is adjudicated or reported with reduced confidence.
- Unsupported findings are rejected.

## Objective and subjective judgment

Evidence-checkable items include claim support, baseline fairness, controls, ablations, statistical reporting, internal numerical consistency, reproducibility detail, and stated limitations.

Partly subjective items include significance, originality, research importance, likely influence, and overall recommendation. Subjective judgments still require rationale and appropriately lower certainty.

## Score anchors

The machine-readable anchors are in `rubrics/icml_review.yaml`.

- Soundness, Presentation, Significance, Originality: 1-4
- Overall Recommendation: 1-6
- Confidence: 1-5

Do not average reviewer scores. Map verified findings to rubric anchors. Apply consistency guards but preserve contextual judgment.

Confidence means confidence in the assessment, not paper quality.

## Final comment

The final Comment should normally include:

1. An accurate two- or three-sentence summary
2. One or two concrete strengths
3. Two or three decision-relevant concerns with locators
4. One or two actionable suggestions

Target approximately 250-450 words unless the API specifies another limit. Avoid generic requests and excessive issue lists.

## Human-alignment stance

The hidden human rubric is not fully observable. Human-like behavior is approximated by accurate summary, balanced strengths, prioritization of decisive issues, severity proportionality, explicit uncertainty, consistent ordinal scoring, and constructive actionable feedback.

Do not claim that the system replaces human judgment or has measured human-level agreement without real labels.
