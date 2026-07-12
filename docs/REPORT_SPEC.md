# Anonymous Four-Page Technical Report Specification

## Recommended title

ReviewHarness: Injection-Resilient Evidence-Weighted Review under Hidden Human Evaluation

## Core research statement

Human-review labels and reviewer-specific heuristics are unavailable during development. ReviewHarness therefore treats Review Agent design as latent human alignment: generating evidence-grounded, decision-relevant, rubric-calibrated reviews while remaining robust to document-borne manipulation and strict production deadlines.

## Primary contributions

Limit the report to three core contributions:

1. Evidence-weighted disagreement resolution that preserves supported minority findings
2. Injection-resilient separation between untrusted paper content and scientific scoring
3. Deadline-aware parallel review with official-rubric ordinal calibration

## Four-page outline

### Page 1: Problem and contributions

Describe hidden human evaluation, why abundant LLM criticism is not the same as good review judgment, the prompt-injection threat, the 25-minute ten-paper constraint, and the three contributions.

### Page 2: Method

Show the pipeline, reviewer perspectives, claim ledger, evidence gate, disagreement states, score calibration, and source/sink security boundary.

### Page 3: Evaluation

Describe baselines, quality proxies, adversarial fixtures, runtime protocol, hands-off iteration method, and exact measured setup. Clearly state that human labels were unavailable.

### Page 4: Results and limitations

Include only measured results, one representative case, batch runtime, security outcome, failure analysis, and limitations.

## Required limitations

- Human labels and private heuristics were unavailable during development
- Proxy metrics do not guarantee human agreement
- Novelty and significance remain partly subjective
- Four-page papers may omit evidence needed for deep verification
- Security scanning cannot prove detection of every visual or encoded attack
- Ten-paper post-hoc correlations have high uncertainty

## Prohibited claims

Do not write “human-level reviewer,” “better than humans,” “optimized human correlation,” or any correlation, attack-success, runtime, or accuracy number not produced by saved artifacts.

## Abstract draft

Large language models can generate plausible criticism of research papers, but reliable peer review requires selecting criticisms that are evidence-supported and decision-relevant while resisting adversarial instructions embedded in submitted documents. We present ReviewHarness, an API-native ICML-style review kernel that treats paper content as untrusted evidence. Independent perspectives assess methodological soundness, evidence and reproducibility, and significance, originality, and presentation. Candidate findings are verified against paper-local evidence and resolved without simple majority voting: agreement informs confidence, whereas evidence strength and impact on central claims determine priority. A dedicated calibrator maps verified findings to the official review scales, while a security boundary isolates scoring from document-borne instructions. The system includes deadline-aware full and fast modes for ten-paper batch execution. Because human judge labels are hidden during development, we evaluate rubric fidelity, evidence grounding, consistency, adversarial robustness, and runtime as transparent proxies for expected human-review alignment.
