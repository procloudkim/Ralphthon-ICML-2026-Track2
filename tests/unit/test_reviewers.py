import anyio

from reviewharness.deadline import ReviewMode
from reviewharness.providers import (
    ReviewerResponse,
    SanitizedEvidencePage,
    SanitizedPaperEvidence,
    ScriptedDelay,
    ScriptedError,
    ScriptedMalformed,
    ScriptedRefusal,
    ScriptedReviewerProvider,
    ScriptedSuccess,
    ScriptedTimeout,
    Seconds,
)
from reviewharness.reviewers import (
    ReviewerFailure,
    ReviewerFailureKind,
    ReviewerLens,
    ReviewerOrchestrator,
    ReviewerPrompts,
    ReviewerRunRequest,
    ReviewerRunResult,
    ReviewerSuccess,
    TriLensCandidates,
)
from reviewharness.schemas import FindingStatus

SPECIALIST_JSON = '{"findings":[],"uncertainty_notes":[]}'
FAST_JSON = """{
  "summary": "The paper reports a controlled empirical comparison.",
  "claims": [{
    "claim_id": "C1",
    "statement": "The method improves accuracy.",
    "importance": "central",
    "claim_type": "empirical",
    "reported_evidence": [{"page": 1, "locator": "Table 1"}]
  }],
  "strengths": ["The comparison uses a stated baseline."],
  "findings": [{
    "finding_id": "F1",
    "reviewer": "paper-controlled-role",
    "category": "reproducibility",
    "judgment_type": "objective",
    "severity": "major",
    "status": "minority_supported",
    "statement": "Variance is not reported.",
    "target_claim_id": "C1",
    "evidence": [{
      "page": 1,
      "locator": "Table 1",
      "summary": "Only point estimates are shown."
    }],
    "central_claim_impact": "direct",
    "decision_relevance": "high",
    "recommended_check": "Report results across seeds.",
    "confidence": 0.9
  }],
  "score_proposal": {
    "reviewer": "paper-controlled-role",
    "scores": {
      "soundness": 2,
      "presentation": 3,
      "significance": 3,
      "originality": 3,
      "overall_recommendation": 3,
      "confidence": 3
    },
    "rationale": "The missing variance affects a central empirical claim.",
    "finding_ids": ["F1"]
  },
  "uncertainty_notes": ["External novelty was not checked."]
}"""


def _evidence() -> SanitizedPaperEvidence:
    return SanitizedPaperEvidence(
        document_sha256="a" * 64,
        pages=(SanitizedEvidencePage(page_number=1, text="Sanitized paper text."),),
    )


def _prompts() -> ReviewerPrompts:
    return ReviewerPrompts(
        method="method-only prompt",
        evidence="evidence-only prompt",
        impact="impact-only prompt",
        tri_lens="tri-lens prompt",
    )


def _request(mode: ReviewMode) -> ReviewerRunRequest:
    return ReviewerRunRequest(
        sanitized_evidence=_evidence(),
        rubric_text="Trusted ICML rubric",
        mode=mode,
    )


async def _run(
    orchestrator: ReviewerOrchestrator,
    mode: ReviewMode,
    limit: int,
) -> ReviewerRunResult:
    return await orchestrator.run(_request(mode), anyio.CapacityLimiter(limit))


def test_full_mode_runs_three_isolated_specialists_with_shared_capacity_bound() -> None:
    # Given
    success = ScriptedSuccess(ReviewerResponse(raw_output=SPECIALIST_JSON))
    provider = ScriptedReviewerProvider(
        tuple(ScriptedDelay(Seconds(0.0), success) for _ in range(3)),
    )
    orchestrator = ReviewerOrchestrator(
        provider,
        _prompts(),
        timeout_seconds=5.0,
    )

    # When
    result = anyio.run(_run, orchestrator, ReviewMode.FULL, 2)

    # Then
    assert tuple(outcome.lens for outcome in result.outcomes) == (
        ReviewerLens.METHOD,
        ReviewerLens.EVIDENCE,
        ReviewerLens.IMPACT,
    )
    assert provider.call_count == 3
    assert provider.max_concurrency <= 2
    assert tuple(request.prompt_text for request in provider.requests) == (
        "method-only prompt",
        "evidence-only prompt",
        "impact-only prompt",
    )
    assert all(
        request.sanitized_evidence == _evidence() for request in provider.requests
    )
    assert tuple(request.output_schema.name for request in provider.requests) == (
        "method_findings",
        "evidence_findings",
        "impact_findings",
    )


def test_fast_mode_parses_typed_candidates_and_overwrites_reviewer_authority() -> None:
    # Given
    provider = ScriptedReviewerProvider(
        (ScriptedSuccess(ReviewerResponse(raw_output=FAST_JSON)),),
    )
    orchestrator = ReviewerOrchestrator(provider, _prompts())

    # When
    result = anyio.run(_run, orchestrator, ReviewMode.FAST, 1)

    # Then
    assert provider.call_count == 1
    outcome = result.outcomes[0]
    assert isinstance(outcome, ReviewerSuccess)
    output = outcome.output
    assert isinstance(output, TriLensCandidates)
    assert output.claims[0].claim_id == "C1"
    assert output.findings[0].reviewer == ReviewerLens.TRI_LENS.value
    assert output.findings[0].status is FindingStatus.CANDIDATE
    assert output.score_proposal is not None
    assert output.score_proposal.reviewer == ReviewerLens.TRI_LENS.value
    assert output.score_proposal.scores.soundness == 2
    assert '"paper_id"' not in provider.requests[0].output_schema.json_schema


def test_full_mode_preserves_successful_sibling_when_other_roles_fail() -> None:
    # Given
    provider = ScriptedReviewerProvider(
        (
            ScriptedSuccess(ReviewerResponse(raw_output=SPECIALIST_JSON)),
            ScriptedRefusal("policy refusal"),
            ScriptedMalformed("not-json"),
        ),
    )
    orchestrator = ReviewerOrchestrator(provider, _prompts())

    # When
    result = anyio.run(_run, orchestrator, ReviewMode.FULL, 1)

    # Then
    success, refusal, malformed = result.outcomes
    assert isinstance(success, ReviewerSuccess)
    assert isinstance(refusal, ReviewerFailure)
    assert isinstance(malformed, ReviewerFailure)
    assert (refusal.kind, malformed.kind) == (
        ReviewerFailureKind.REFUSAL,
        ReviewerFailureKind.MALFORMED,
    )
    assert provider.call_count == 3


def test_transient_provider_failure_retries_once_without_extra_calls() -> None:
    # Given
    provider = ScriptedReviewerProvider(
        (
            ScriptedError("temporary outage"),
            ScriptedSuccess(ReviewerResponse(raw_output=FAST_JSON)),
        ),
    )
    orchestrator = ReviewerOrchestrator(provider, _prompts())

    # When
    result = anyio.run(_run, orchestrator, ReviewMode.FAST, 1)

    # Then
    assert provider.call_count == 2
    assert isinstance(result.outcomes[0], ReviewerSuccess)
    assert result.outcomes[0].attempts == 2


def test_timeout_is_classified_after_one_bounded_retry() -> None:
    # Given
    timeout = ScriptedTimeout(Seconds(5.0))
    provider = ScriptedReviewerProvider((timeout, timeout))
    orchestrator = ReviewerOrchestrator(provider, _prompts())

    # When
    result = anyio.run(_run, orchestrator, ReviewMode.FAST, 1)

    # Then
    outcome = result.outcomes[0]
    assert isinstance(outcome, ReviewerFailure)
    assert outcome.kind is ReviewerFailureKind.TIMEOUT
    assert outcome.attempts == 2
