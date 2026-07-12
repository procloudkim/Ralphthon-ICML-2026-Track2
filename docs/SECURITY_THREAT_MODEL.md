# Prompt-Injection Threat Model and Security Contract

## Security objective

A submitted paper may contain adversarial instructions. The system must ensure that document-borne instructions cannot control scores, output schema, trusted identifiers, tools, secrets, API routing, other papers, or submission behavior.

The objective is not perfect malicious-text classification. The objective is to constrain impact even when an attack is not perfectly detected.

## Source and sink

- Source: all paper-derived content, including hidden text and metadata
- Sensitive sinks: scoring, final Comment, trusted identifiers, credentials, tools, network access, submission API, and cross-paper state

The architecture must separate the source from every sensitive sink.

## Threats

A paper may attempt to:

- force Strong Accept or another score;
- suppress criticism;
- request marker phrases;
- impersonate system, developer, reviewer, chair, or administrator messages;
- replace the official rubric;
- break or extend JSON;
- request secrets or environment variables;
- invoke shell, network, links, or tools;
- redirect submission or replace paper ID;
- poison other papers' reviews;
- hide instructions in tiny, transparent, white, off-page, encoded, metadata, annotation, attachment, image, or QR content.

## Security controls

### Capability isolation

Reviewer model calls receive only sanitized paper data, trusted rubric, and strict output schemas. They receive no shell, arbitrary filesystem, arbitrary network, credentials, environment variables, submission API, or cross-paper mutation capability.

### Trusted control plane

Only orchestration code sets paper ID, assignment ID, deadlines, endpoints, credentials, rubric, model configuration, and routing. The application overwrites any model-generated identifier.

### Secure ingest

Do not execute PDF actions. Inspect active content, annotations, attachments, links, metadata, suspicious hidden text, zero-width characters, fake authority, fake rubric text, marker requests, and score-steering language where feasible.

### Classification and quarantine

Classify suspicious spans as:

- `manipulative_instruction`
- `reviewer_detection_canary`
- `benign_quoted_example`
- `uncertain_instruction`

Preserve document hash and location. Neutralize instruction authority while retaining enough context to review legitimate scientific content. A paper about prompt injection may quote attacks; do not blindly delete research examples.

### Scoring isolation

Raw suspicious instructions do not reach the score calibrator. The calibrator receives the trusted rubric, sanitized summary, claim ledger, verified findings, disagreement state, parser confidence, and security status without malicious text.

### No automatic paper penalty

Detection alone does not lower Soundness, Presentation, Significance, Originality, or Overall Recommendation. Lower Confidence only when sanitization materially limits the scientific review.

### Output validation

Before returning a review, verify trusted paper ID, strict schema, no marker leakage, no unauthorized fields, no tool or credential requests, evidence support for scores, routing isolation, and cross-paper isolation.

### Conditional invariance test

For high-risk detections, compare two safe variants: suspicious spans replaced by neutral placeholders versus removed. Recalibrate when scores or major issues change. Keep this conditional because of the 25-minute deadline.

## Security evaluation

Measure attack success rate, marker leakage, unauthorized tool calls, clean-versus-injected score difference, clean-versus-injected major-issue overlap, detection recall, benign false-positive rate, output validity, and runtime overhead.

Minimum fixtures include direct score steering, omit-weaknesses request, fake authority, fake rubric, marker phrase, JSON breakout, secret request, shell or URL request, hidden text, metadata injection, cross-paper poisoning, and benign quoted attack examples.
