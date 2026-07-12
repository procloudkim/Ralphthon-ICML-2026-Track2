# Evaluation Specification

## Hidden human labels

The organizers' human judge scores and private heuristics are unavailable during development. Actual human correlation is therefore `N/A` until real labels exist.

Do not fabricate labels, call synthetic fixtures human ground truth, or report invented correlations. Do not force a guessed score distribution or target accept rate.

## Primary development proxies

### Scientific review quality

- Official rubric-anchor compliance
- Summary accuracy
- Evidence coverage
- Unsupported critique rate
- Controlled-fixture issue recall
- Controlled-fixture issue precision
- Severity proportionality
- Score-comment consistency
- Finding compression
- Repeatability
- Top-issue stability
- Appropriate confidence

### Security

- Prompt-injection attack success rate
- Marker leakage rate
- Unauthorized tool-call count
- Clean-versus-injected score delta
- Clean-versus-injected issue overlap
- Detection recall
- Benign false-positive rate
- Valid completion rate
- Security overhead

### Reliability and runtime

- Ten-of-ten valid completion
- Total batch time
- p50 and p95 paper time
- Timeout count
- Retry count
- Fast-mode fallback count
- Invalid output count
- Submission success and receipt verification

## Baselines and ablations

Where time permits, compare:

1. `single_pass`: one model produces final review directly
2. `majority_ensemble`: three reviews with averaging or majority voting
3. `evidence_resolver`: evidence gate and minority-finding preservation
4. `secure_evidence_resolver`: resolver plus structural injection defense

Useful ablations include one versus three perspectives, majority versus evidence-weighted resolution, prompt-only defense versus source/sink separation, full versus fast mode, and optional batch consistency audit.

Every experiment changes one component, reruns the same cases, records before and after metrics, runtime, security impact, and a keep-or-revert decision.

## Post-hoc human evaluation

Only after real labels exist, compute:

- Overall Recommendation: Pearson correlation, Spearman correlation, MAE, weighted kappa
- Dimension scores: per-dimension MAE and correlations when sample size permits
- Review content: major-issue semantic overlap, severity agreement, and decision-relevant issue recall

Ten papers are a small sample. Report uncertainty and avoid strong statistical claims.

## Stopping rule

P0 completion and ten-paper deadline compatibility take precedence over marginal proxy improvements. Security regressions are blocking. Stop optional optimization by 15:10 and preserve the best verified configuration.
