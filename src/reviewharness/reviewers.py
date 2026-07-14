"""Bounded tool-less orchestration for independent reviewer calls."""

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Final, Self, final

import anyio
from pydantic import BaseModel, ConfigDict, ValidationError

from .deadline import ReviewMode
from .provider_contracts import ProviderClaim, ProviderFinding
from .providers import (
    OutputSchemaDeclaration,
    ProviderCallError,
    ProviderError,
    ProviderRefusalError,
    ProviderTimeoutError,
    ReviewerProvider,
    ReviewerRequest,
    SanitizedEvidencePage,
    SanitizedPaperEvidence,
)
from .schemas import (
    NonEmptyStr,
    PaperClaim,
    ReviewFinding,
    ScoreProposal,
)


class _StrictReviewerModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class SpecialistCandidates(_StrictReviewerModel):
    """Candidate findings produced by one full-mode specialist."""

    findings: tuple[ProviderFinding, ...]
    uncertainty_notes: tuple[NonEmptyStr, ...]


class TriLensCandidates(SpecialistCandidates):
    """Fast-mode claims, findings, and mandatory rubric score proposal."""

    summary: NonEmptyStr
    claims: tuple[ProviderClaim, ...]
    strengths: tuple[NonEmptyStr, ...]
    score_proposal: ScoreProposal


class CalibrationEvidence(_StrictReviewerModel):
    """Canonical scientific structures allowed into the score-only call."""

    claims: tuple[PaperClaim, ...]
    findings: tuple[ReviewFinding, ...]


class ScoreCalibratorCandidates(_StrictReviewerModel):
    """Dedicated full-mode score proposal awaiting trusted source overwrite."""

    score_proposal: ScoreProposal
    uncertainty_notes: tuple[NonEmptyStr, ...]


_SPECIALIST_SCHEMA = json.dumps(SpecialistCandidates.model_json_schema())
_TRI_LENS_SCHEMA = json.dumps(TriLensCandidates.model_json_schema())
_SCORE_CALIBRATOR_SCHEMA = json.dumps(ScoreCalibratorCandidates.model_json_schema())


class ReviewerLens(StrEnum):
    """Trusted role assigned to one capability-limited reviewer call."""

    METHOD = "method"
    EVIDENCE = "evidence"
    IMPACT = "impact"
    TRI_LENS = "tri_lens"
    SCORE_CALIBRATOR = "full_calibrator"


class ReviewerFailureKind(StrEnum):
    """Expected failure classes retained for fallback decisions."""

    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    MALFORMED = "malformed"
    PROVIDER = "provider"


@dataclass(frozen=True, slots=True)
class ReviewerPrompts:
    """Trusted standalone prompts for every supported reviewer lens."""

    method: str
    evidence: str
    impact: str
    tri_lens: str
    score_calibrator: str

    @classmethod
    def from_directory(cls, directory: Path) -> Self:
        """Load the four trusted reviewer prompts."""
        return cls(
            method=(directory / "method_reviewer.md").read_text(encoding="utf-8"),
            evidence=(directory / "evidence_reviewer.md").read_text(encoding="utf-8"),
            impact=(directory / "impact_reviewer.md").read_text(encoding="utf-8"),
            tri_lens=(directory / "tri_lens_reviewer.md").read_text(encoding="utf-8"),
            score_calibrator=(directory / "score_calibrator.md").read_text(
                encoding="utf-8"
            ),
        )


class ReviewerRunRequest(_StrictReviewerModel):
    """Trusted inputs shared by calls without sharing reviewer outputs."""

    sanitized_evidence: SanitizedPaperEvidence
    rubric_text: NonEmptyStr
    mode: ReviewMode


@dataclass(frozen=True, slots=True)
class ReviewerSuccess:
    """One strictly parsed reviewer response in trusted role context."""

    lens: ReviewerLens
    attempts: int
    output: SpecialistCandidates | TriLensCandidates | ScoreCalibratorCandidates
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class ReviewerFailure:
    """One expected reviewer failure that does not cancel sibling calls."""

    lens: ReviewerLens
    kind: ReviewerFailureKind
    attempts: int
    detail: str
    retryable: bool


type ReviewerOutcome = ReviewerSuccess | ReviewerFailure

_OUTPUT_CONTROLS: Final = {
    ReviewerLens.METHOD: ("method_findings", _SPECIALIST_SCHEMA),
    ReviewerLens.EVIDENCE: ("evidence_findings", _SPECIALIST_SCHEMA),
    ReviewerLens.IMPACT: ("impact_findings", _SPECIALIST_SCHEMA),
    ReviewerLens.TRI_LENS: ("tri_lens_review", _TRI_LENS_SCHEMA),
    ReviewerLens.SCORE_CALIBRATOR: (
        "score_calibration",
        _SCORE_CALIBRATOR_SCHEMA,
    ),
}
_LENSES_BY_MODE: Final = {
    ReviewMode.FULL: (ReviewerLens.METHOD, ReviewerLens.EVIDENCE, ReviewerLens.IMPACT),
    ReviewMode.FAST: (ReviewerLens.TRI_LENS,),
}
_PROVIDER_FAILURE_KINDS: Final[dict[type[ProviderError], ReviewerFailureKind]] = {
    ProviderRefusalError: ReviewerFailureKind.REFUSAL,
    ProviderTimeoutError: ReviewerFailureKind.TIMEOUT,
    ProviderCallError: ReviewerFailureKind.PROVIDER,
}


@dataclass(frozen=True, slots=True)
class ReviewerRunResult:
    """Deterministically ordered reviewer outcomes."""

    outcomes: tuple[ReviewerOutcome, ...]


@final
class ReviewerOrchestrator:
    """Run full or fast reviewer calls through one provider-only boundary."""

    def __init__(
        self,
        provider: ReviewerProvider,
        prompts: ReviewerPrompts,
        timeout_seconds: float = 120.0,
        *,
        retry_failures: bool = True,
    ) -> None:
        """Bind one provider, trusted prompt set, and call policy."""
        self._provider = provider
        self._prompts = prompts
        self._timeout_seconds = timeout_seconds
        self._retry_failures = retry_failures

    async def run(
        self,
        request: ReviewerRunRequest,
        limiter: anyio.CapacityLimiter,
    ) -> ReviewerRunResult:
        """Run role calls concurrently and retain deterministic role order."""
        lenses = _LENSES_BY_MODE[request.mode]
        ordered: list[ReviewerOutcome] = []
        async with anyio.create_task_group() as task_group:
            handles = tuple(
                task_group.create_task(self._call_lens(lens, request, limiter))
                for lens in lenses
            )
            ordered.extend([await handle for handle in handles])
        return ReviewerRunResult(outcomes=tuple(ordered))

    async def calibrate(
        self,
        document_sha256: str,
        claims: tuple[PaperClaim, ...],
        findings: tuple[ReviewFinding, ...],
        rubric_text: str,
        limiter: anyio.CapacityLimiter,
    ) -> ReviewerOutcome:
        """Run one score-only call over canonical structures, never raw paper text."""
        calibration = CalibrationEvidence(claims=claims, findings=findings)
        request = ReviewerRunRequest(
            sanitized_evidence=SanitizedPaperEvidence(
                document_sha256=document_sha256,
                pages=(
                    SanitizedEvidencePage(
                        page_number=1,
                        text=calibration.model_dump_json(),
                    ),
                ),
                security_notes=("canonical_claims_and_findings_only",),
            ),
            rubric_text=rubric_text,
            mode=ReviewMode.FULL,
        )
        return await self._call_lens(ReviewerLens.SCORE_CALIBRATOR, request, limiter)

    async def _call_lens(
        self,
        lens: ReviewerLens,
        run_request: ReviewerRunRequest,
        limiter: anyio.CapacityLimiter,
    ) -> ReviewerOutcome:
        provider_request = self._provider_request(lens, run_request)
        first = await self._attempt(lens, provider_request, limiter, attempts=1)
        if self._retry_failures and first.retryable:
            return await self._attempt(lens, provider_request, limiter, attempts=2)
        return first

    async def _attempt(
        self,
        lens: ReviewerLens,
        request: ReviewerRequest,
        limiter: anyio.CapacityLimiter,
        *,
        attempts: int,
    ) -> ReviewerOutcome:
        try:
            with anyio.fail_after(self._timeout_seconds):
                async with limiter:
                    try:
                        response = await self._provider.review(request)
                    except ProviderError as error:
                        return self._provider_failure(lens, attempts, error)
        except TimeoutError:
            return ReviewerFailure(
                lens,
                ReviewerFailureKind.TIMEOUT,
                attempts,
                f"call exceeded {self._timeout_seconds} seconds",
                retryable=True,
            )

        try:
            return self._parse(lens, response.raw_output, attempts)
        except ValidationError as error:
            return ReviewerFailure(
                lens,
                ReviewerFailureKind.MALFORMED,
                attempts,
                str(error),
                retryable=False,
            )

    def _parse(
        self, lens: ReviewerLens, raw_output: str, attempts: int
    ) -> ReviewerSuccess:
        if lens is ReviewerLens.TRI_LENS:
            parsed = TriLensCandidates.model_validate_json(raw_output, strict=True)
            proposal = parsed.score_proposal.model_copy(update={"reviewer": lens.value})
            output = parsed.model_copy(
                update={
                    "score_proposal": proposal,
                },
            )
        elif lens is ReviewerLens.SCORE_CALIBRATOR:
            parsed_calibration = ScoreCalibratorCandidates.model_validate_json(
                raw_output,
                strict=True,
            )
            output = parsed_calibration.model_copy(
                update={
                    "score_proposal": parsed_calibration.score_proposal.model_copy(
                        update={"reviewer": lens.value}
                    )
                }
            )
        else:
            output = SpecialistCandidates.model_validate_json(raw_output, strict=True)
        return ReviewerSuccess(lens=lens, attempts=attempts, output=output)

    @staticmethod
    def _provider_failure(
        lens: ReviewerLens,
        attempts: int,
        error: ProviderError,
    ) -> ReviewerFailure:
        kind = _PROVIDER_FAILURE_KINDS.get(type(error), ReviewerFailureKind.PROVIDER)
        retryable = kind in (ReviewerFailureKind.TIMEOUT, ReviewerFailureKind.PROVIDER)
        return ReviewerFailure(lens, kind, attempts, str(error), retryable)

    def _provider_request(
        self,
        lens: ReviewerLens,
        request: ReviewerRunRequest,
    ) -> ReviewerRequest:
        prompt = {
            ReviewerLens.METHOD: self._prompts.method,
            ReviewerLens.EVIDENCE: self._prompts.evidence,
            ReviewerLens.IMPACT: self._prompts.impact,
            ReviewerLens.TRI_LENS: self._prompts.tri_lens,
            ReviewerLens.SCORE_CALIBRATOR: self._prompts.score_calibrator,
        }[lens]
        name, schema = _OUTPUT_CONTROLS[lens]
        return ReviewerRequest(
            sanitized_evidence=request.sanitized_evidence,
            rubric_text=request.rubric_text,
            prompt_text=prompt,
            output_schema=OutputSchemaDeclaration(name=name, json_schema=schema),
        )
