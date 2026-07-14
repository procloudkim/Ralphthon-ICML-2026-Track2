"""Isolated single-paper evidence-grounded review kernel."""

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
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


def _fallback(*, degraded: bool) -> schemas.ScoreProposal:
    return schemas.ScoreProposal(
        reviewer="trusted-local-fallback",
        scores=schemas.ReviewScores(
            soundness=3,
            presentation=3,
            significance=2,
            originality=2,
            overall_recommendation=3,
            confidence=2 if degraded else 3,
        ),
        rationale="Conservative rubric baseline pending stronger verified evidence.",
    )


@final
class ReviewKernel:
    """Compose the capability-limited local review pipeline for one paper."""

    __slots__ = (
        "_confidence_cap",
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
        except (
            artifacts.ArtifactPathError,
            OSError,
            parser.PdfParseError,
            validation.ReviewValidationError,
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
                "reviewer_provider_unavailable",
            )
        ledger = claims.build_claim_ledger(reviewer_data.claims, prepared.blocks)
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
        proposal = (
            reviewer_data.proposals[0]
            if reviewer_data.proposals
            else _fallback(
                degraded=reviewer_data.failures > 0 or not reviewer_data.outputs
            )
        )
        calibration = scoring.calibrate_scores(
            scoring.CalibrationContext(
                proposal=proposal,
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
                        "heuristic fallback."
                    ),
                }
            )
        comment = formatter.build_review_comment(
            ledger,
            resolution.retained,
            calibration,
        )
        submission = schemas.compose_review_submission(
            assignment,
            calibration,
            comment,
        )
        validated = validation.validate_review_submission(
            submission,
            validation.ReviewValidationContext(
                assignment,
                resolution.retained,
                calibration,
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
                validated,
            ),
        )
        return validated
