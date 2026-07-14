"""Strict domain and boundary schemas for ReviewHarness."""

from datetime import datetime
from enum import StrEnum, unique
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
)

type NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
type PositiveInt = Annotated[StrictInt, Field(ge=1)]
type DimensionScore = Annotated[StrictInt, Field(ge=1, le=4)]
type RecommendationScore = Annotated[StrictInt, Field(ge=1, le=6)]
type ConfidenceScore = Annotated[StrictInt, Field(ge=1, le=5)]
type UnitFloat = Annotated[
    float,
    Field(ge=0.0, le=1.0, allow_inf_nan=False),
]
type ReviewComment = Annotated[str, StringConstraints(min_length=100, max_length=12000)]
type Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


@unique
class ClaimImportance(StrEnum):
    """Importance of a paper claim to the contribution."""

    CENTRAL = "central"
    SUPPORTING = "supporting"
    BACKGROUND = "background"


@unique
class ClaimType(StrEnum):
    """Scientific claim category used by the claim ledger."""

    EMPIRICAL = "empirical"
    THEORETICAL = "theoretical"
    METHODOLOGICAL = "methodological"
    DATASET = "dataset"
    SYSTEMS = "systems"
    ANALYSIS = "analysis"
    POSITION = "position"
    OTHER = "other"


@unique
class JudgmentType(StrEnum):
    """Degree to which a finding is objectively checkable."""

    OBJECTIVE = "objective"
    SUBJECTIVE = "subjective"
    MIXED = "mixed"


@unique
class FindingSeverity(StrEnum):
    """Decision severity of a review finding."""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    OBSERVATION = "observation"


@unique
class FindingStatus(StrEnum):
    """Evidence-resolution state of a review finding."""

    CANDIDATE = "candidate"
    CONSENSUS_SUPPORTED = "consensus_supported"
    MINORITY_SUPPORTED = "minority_supported"
    CONTESTED = "contested"
    UNSUPPORTED_REJECTED = "unsupported_rejected"
    UNSUPPORTED_HYPOTHESIS = "unsupported_hypothesis"
    SUBJECTIVE_DIVERGENCE = "subjective_divergence"
    PARSER_UNCERTAIN = "parser_uncertain"


@unique
class CentralClaimImpact(StrEnum):
    """Relationship between a finding and a central paper claim."""

    DIRECT = "direct"
    INDIRECT = "indirect"
    NONE = "none"
    UNCERTAIN = "uncertain"


@unique
class DecisionRelevance(StrEnum):
    """Expected influence of a finding on the final recommendation."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@unique
class InjectionClassification(StrEnum):
    """Security classification for suspicious paper-derived text."""

    MANIPULATIVE_INSTRUCTION = "manipulative_instruction"
    REVIEWER_DETECTION_CANARY = "reviewer_detection_canary"
    BENIGN_QUOTED_EXAMPLE = "benign_quoted_example"
    UNCERTAIN_INSTRUCTION = "uncertain_instruction"


@unique
class ScoreSource(StrEnum):
    """Trusted origin of the proposal used for final score calibration."""

    TRI_LENS = "tri_lens"
    FULL_CALIBRATOR = "full_calibrator"
    LOCAL_OFFLINE = "local_offline"


class TrustedAssignment(_StrictModel):
    """Trusted control-plane metadata for one assigned paper."""

    paper_id: NonEmptyStr
    pdf_path: Path
    title: NonEmptyStr | None = None
    assignment_id: NonEmptyStr | None = None
    ordinal: PositiveInt | None = None
    deadline_at: datetime | None = None


class PdfBlock(_StrictModel):
    """Page-aware text block extracted without executing PDF content."""

    block_id: NonEmptyStr
    page: PositiveInt
    text: NonEmptyStr
    section: NonEmptyStr | None = None
    locator: NonEmptyStr | None = None


class ClaimLocator(_StrictModel):
    """Paper-local location reported in support of a claim."""

    page: PositiveInt
    section: NonEmptyStr | None = None
    locator: NonEmptyStr | None = None
    block_id: NonEmptyStr | None = None


class EvidenceLocator(_StrictModel):
    """Paper-local evidence supporting a review finding."""

    page: PositiveInt
    section: NonEmptyStr | None = None
    locator: NonEmptyStr | None = None
    summary: NonEmptyStr
    block_id: NonEmptyStr | None = None


class PaperClaim(_StrictModel):
    """One typed entry in the paper claim ledger."""

    claim_id: NonEmptyStr
    statement: NonEmptyStr
    importance: ClaimImportance
    claim_type: ClaimType
    reported_evidence: tuple[ClaimLocator, ...] = ()


class ReviewFinding(_StrictModel):
    """Canonical evidence-grounded review finding."""

    finding_id: NonEmptyStr
    reviewer: NonEmptyStr | None = None
    category: NonEmptyStr
    judgment_type: JudgmentType
    severity: FindingSeverity
    status: FindingStatus
    statement: NonEmptyStr
    target_claim_id: NonEmptyStr | None = None
    evidence: tuple[EvidenceLocator, ...]
    central_claim_impact: CentralClaimImpact
    decision_relevance: DecisionRelevance
    recommended_check: NonEmptyStr | None = None
    confidence: UnitFloat
    priority: UnitFloat | None = None


class ReviewScores(_StrictModel):
    """Official ICML ordinal review scores without an identifier."""

    soundness: DimensionScore
    presentation: DimensionScore
    significance: DimensionScore
    originality: DimensionScore
    overall_recommendation: RecommendationScore
    confidence: ConfidenceScore


class ScoreProposal(_StrictModel):
    """Reviewer score proposal linked to its supporting findings."""

    reviewer: NonEmptyStr
    scores: ReviewScores
    rationale: NonEmptyStr
    finding_ids: tuple[NonEmptyStr, ...] = ()


class ScoreCalibration(_StrictModel):
    """Rubric-calibrated scores and deterministic consistency trace."""

    scores: ReviewScores
    source: ScoreSource
    retained_finding_ids: tuple[NonEmptyStr, ...] = ()
    rejected_finding_ids: tuple[NonEmptyStr, ...] = ()
    rationale: NonEmptyStr
    consistency_guards_passed: StrictBool


class CommentInclusionTrace(_StrictModel):
    """Application-owned identifiers actually rendered into the final comment."""

    included_claim_ids: tuple[NonEmptyStr, ...] = ()
    included_finding_ids: tuple[NonEmptyStr, ...] = ()


class SecurityDetection(_StrictModel):
    """Safe summary of one suspicious paper-derived span."""

    classification: InjectionClassification
    page: PositiveInt | None = None
    block_id: NonEmptyStr | None = None
    summary: NonEmptyStr
    quarantined: StrictBool


class SecurityReport(_StrictModel):
    """Aggregate secure-ingest result without raw attack text."""

    document_sha256: Sha256
    detections: tuple[SecurityDetection, ...] = ()
    active_content_detected: StrictBool = False
    annotations_detected: StrictBool = False
    attachments_detected: StrictBool = False
    links_detected: StrictBool = False
    sanitization_limited_review: StrictBool = False


class ReviewSubmission(_StrictModel):
    """Exact validated internal payload for one final review."""

    paper_id: NonEmptyStr
    soundness: DimensionScore
    presentation: DimensionScore
    significance: DimensionScore
    originality: DimensionScore
    overall_recommendation: RecommendationScore
    confidence: ConfidenceScore
    comment: ReviewComment


def compose_review_submission(
    assignment: TrustedAssignment,
    calibration: ScoreCalibration,
    comment: ReviewComment,
) -> ReviewSubmission:
    """Compose output with the paper identifier from trusted metadata only."""
    scores = calibration.scores
    return ReviewSubmission(
        paper_id=assignment.paper_id,
        soundness=scores.soundness,
        presentation=scores.presentation,
        significance=scores.significance,
        originality=scores.originality,
        overall_recommendation=scores.overall_recommendation,
        confidence=scores.confidence,
        comment=comment,
    )
