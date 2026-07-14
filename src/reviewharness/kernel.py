"""Isolated single-paper evidence-grounded review kernel."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, final, override

import anyio
from anyio.to_thread import run_sync

import reviewharness.kernel_support as support
from reviewharness import (
    artifacts,
    claims,
    evidence,
    formatter,
    parser,
    reviewers,
    schemas,
    scoring,
    secure_ingest,
    validation,
)
from reviewharness.config import RubricConfig, load_rubric
from reviewharness.deadline import ReviewMode
from reviewharness.local_provider import LocalHeuristicProvider
from reviewharness.providers import ReviewerProvider

_PROMPTS: Final = Path(__file__).resolve().parents[2] / "prompts"
_CALLS: Final = {ReviewMode.FULL: 3, ReviewMode.FAST: 1}


@dataclass(slots=True)
class KernelReviewError(RuntimeError):
    """Expected per-paper failure safe for batch isolation."""

    paper_id: str
    failure_kind: str

    @override
    def __str__(self) -> str:
        return f"paper review failed for {self.paper_id}: {self.failure_kind}"


@dataclass(frozen=True, slots=True)
class ReviewKernelPolicy:
    """Trusted provider-call and fallback policy for one kernel instance."""

    timeout_seconds: float = 120.0
    require_reviewer_output: bool = False
    retry_reviewer_failures: bool = True
    confidence_cap: int | None = None


@final
class ReviewKernel:
    """Compose the capability-limited local review pipeline for one paper."""

    __slots__ = (
        "_confidence_cap",
        "_is_local_offline",
        "_orchestrator",
        "_require_reviewer_output",
        "_rubric",
    )

    def __init__(
        self,
        provider: ReviewerProvider | None = None,
        *,
        rubric: RubricConfig | None = None,
        prompts_directory: Path = _PROMPTS,
        policy: ReviewKernelPolicy | None = None,
    ) -> None:
        """Bind trusted rubric/prompts and a capability-limited provider."""
        selected = LocalHeuristicProvider() if provider is None else provider
        selected_policy = ReviewKernelPolicy() if policy is None else policy
        self._is_local_offline = isinstance(selected, LocalHeuristicProvider)
        self._rubric = load_rubric() if rubric is None else rubric
        self._require_reviewer_output = selected_policy.require_reviewer_output
        self._confidence_cap = selected_policy.confidence_cap
        prompts = reviewers.ReviewerPrompts.from_directory(prompts_directory)
        self._orchestrator = reviewers.ReviewerOrchestrator(
            selected,
            prompts,
            selected_policy.timeout_seconds,
            retry_failures=selected_policy.retry_reviewer_failures,
        )

    async def review(
        self,
        assignment: schemas.TrustedAssignment,
        mode: ReviewMode,
        output_dir: Path,
        shared_limiter: anyio.CapacityLimiter | None = None,
    ) -> schemas.ReviewSubmission:
        """Return one validated review or a typed isolated paper failure."""
        try:
            return await self._review(assignment, mode, output_dir, shared_limiter)
        except validation.ReviewValidationError as error:
            raise KernelReviewError(
                assignment.paper_id,
                "semantic_validation_failure",
            ) from error
        except (
            artifacts.ArtifactPathError,
            OSError,
            parser.PdfParseError,
        ) as error:
            raise KernelReviewError(
                assignment.paper_id,
                type(error).__name__,
            ) from error

    async def _review(
        self,
        assignment: schemas.TrustedAssignment,
        mode: ReviewMode,
        output_dir: Path,
        shared_limiter: anyio.CapacityLimiter | None,
    ) -> schemas.ReviewSubmission:
        ingest = await run_sync(
            secure_ingest.ingest_pdf,
            assignment.pdf_path,
            abandon_on_cancel=True,
        )
        prepared = support.prepare_evidence(ingest)
        limiter = (
            anyio.CapacityLimiter(_CALLS[mode])
            if shared_limiter is None
            else shared_limiter
        )
        run = await self._orchestrator.run(
            reviewers.ReviewerRunRequest(
                sanitized_evidence=prepared.provider_evidence,
                rubric_text=self._rubric.model_dump_json(),
                mode=mode,
            ),
            limiter,
        )
        reviewer_data = support.collect_reviewer_data(run, prepared.blocks)
        if self._require_reviewer_output and not reviewer_data.outputs:
            raise KernelReviewError(
                assignment.paper_id,
                _reviewer_failure_kind(run),
            )
        if (
            not self._is_local_offline
            and mode is ReviewMode.FAST
            and reviewer_data.contract_stats.accepted_claims == 0
        ):
            raise KernelReviewError(
                assignment.paper_id,
                "evidence_contract_failure",
            )
        ledger = claims.build_claim_ledger(reviewer_data.claims, prepared.blocks)
        if not ledger:
            raise KernelReviewError(
                assignment.paper_id,
                "unreviewable_empty_claim_ledger",
            )
        linked_findings = support.relink_findings(
            reviewer_data.findings,
            reviewer_data.claims,
            ledger,
        )
        resolution = evidence.verify_and_resolve(
            linked_findings,
            prepared.blocks,
            ledger,
        )
        if mode is ReviewMode.FAST:
            if len(reviewer_data.proposals) != 1:
                raise KernelReviewError(
                    assignment.paper_id,
                    "score_provenance_failure",
                )
            proposal = reviewer_data.proposals[0]
            score_source = (
                schemas.ScoreSource.LOCAL_OFFLINE
                if self._is_local_offline
                else schemas.ScoreSource.TRI_LENS
            )
        else:
            calibration_outcome = await self._orchestrator.calibrate(
                prepared.provider_evidence.document_sha256,
                ledger,
                resolution.retained + resolution.rejected,
                self._rubric.model_dump_json(),
                limiter,
            )
            if not isinstance(
                calibration_outcome, reviewers.ReviewerSuccess
            ) or not isinstance(
                calibration_outcome.output,
                reviewers.ScoreCalibratorCandidates,
            ):
                raise KernelReviewError(
                    assignment.paper_id,
                    _reviewer_outcome_failure_kind(calibration_outcome),
                )
            calibration_output = calibration_outcome.output
            proposal = calibration_output.score_proposal
            reviewer_data = replace(
                reviewer_data,
                outputs=(*reviewer_data.outputs, calibration_output),
                proposals=(proposal,),
            )
            score_source = (
                schemas.ScoreSource.LOCAL_OFFLINE
                if self._is_local_offline
                else schemas.ScoreSource.FULL_CALIBRATOR
            )
        calibration = scoring.calibrate_scores(
            scoring.CalibrationContext(
                proposal=proposal,
                source=score_source,
                findings=resolution.retained + resolution.rejected,
                parser_confidence=1.0 if prepared.blocks else 0.2,
                reviewer_disagreement=reviewer_data.failures / _CALLS[mode],
                sanitization_limited_review=(
                    bool(ingest.security.findings) and not prepared.blocks
                ),
            ),
            self._rubric,
        )
        if (
            self._confidence_cap is not None
            and calibration.scores.confidence > self._confidence_cap
        ):
            capped_scores = calibration.scores.model_copy(
                update={"confidence": self._confidence_cap}
            )
            calibration = calibration.model_copy(
                update={
                    "scores": capped_scores,
                    "rationale": (
                        f"{calibration.rationale} Confidence is capped for "
                        "the explicit offline provider."
                    ),
                }
            )
        formatted = formatter.build_review_comment(
            ledger,
            resolution.retained,
            calibration,
        )
        submission = schemas.compose_review_submission(
            assignment,
            calibration,
            formatted.comment,
        )
        validated = validation.validate_review_submission(
            submission,
            validation.ReviewValidationContext(
                assignment,
                ledger,
                resolution.retained,
                calibration,
                formatted.trace,
            ),
        ).require_valid()
        support.persist_trace(
            output_dir,
            assignment,
            support.KernelTrace(
                mode,
                ingest,
                prepared,
                reviewer_data,
                ledger,
                resolution,
                calibration,
                formatted.trace,
                validated,
            ),
        )
        return validated


def _reviewer_failure_kind(run: reviewers.ReviewerRunResult) -> str:
    failures = tuple(
        outcome
        for outcome in run.outcomes
        if isinstance(outcome, reviewers.ReviewerFailure)
    )
    if any(
        failure.kind is reviewers.ReviewerFailureKind.SCORE_PROVENANCE
        for failure in failures
    ):
        return "score_provenance_failure"
    if failures and all(failure.retryable for failure in failures):
        return "transient_provider_failure"
    return "provider_failure"


def _reviewer_outcome_failure_kind(outcome: reviewers.ReviewerOutcome) -> str:
    if isinstance(outcome, reviewers.ReviewerFailure):
        if outcome.kind is reviewers.ReviewerFailureKind.SCORE_PROVENANCE:
            return "score_provenance_failure"
        if outcome.retryable:
            return "transient_provider_failure"
    return "provider_failure"
