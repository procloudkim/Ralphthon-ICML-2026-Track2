"""Strict boundary and domain conversion for controlled quality cases."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from reviewharness.schemas import (
    CentralClaimImpact,
    ClaimImportance,
    ClaimType,
    DecisionRelevance,
    EvidenceLocator,
    FindingSeverity,
    FindingStatus,
    JudgmentType,
    PaperClaim,
    PdfBlock,
    ReviewFinding,
    ReviewScores,
)

type CaseType = Literal[
    "supported_minority_central",
    "unsupported_factual_major",
    "objective_consensus",
    "contested_subjective",
    "score_consistency",
]
type ExpectedAction = Literal["retain", "reject"]

_ROOT: Final = Path(__file__).resolve().parents[2]
_CORPUS_PATH: Final = _ROOT / "tests" / "fixtures" / "quality" / "cases.json"


class _StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class PageEvidence(_StrictModel):
    """One controlled paper-local passage."""

    evidence_id: str = Field(min_length=1)
    page: StrictInt = Field(ge=1)
    locator: str = Field(min_length=1)
    text: str = Field(min_length=1)


class CandidateFinding(_StrictModel):
    """One controlled finding proposed by one or more perspectives."""

    finding_id: str = Field(min_length=1)
    reviewer_count: StrictInt = Field(ge=1, le=3)
    judgment_type: JudgmentType
    severity: FindingSeverity
    statement: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]


class FindingExpectation(_StrictModel):
    """Synthetic expected action for one controlled finding."""

    finding_id: str = Field(min_length=1)
    expected_action: ExpectedAction
    expected_status: FindingStatus
    reason: str = Field(min_length=1)


class QualityCase(_StrictModel):
    """Strict boundary model for one controlled quality case."""

    case_id: str = Field(min_length=1)
    case_type: CaseType
    paper_path: str = Field(min_length=1)
    page_evidence: tuple[PageEvidence, ...]
    candidate_findings: tuple[CandidateFinding, ...]
    expectations: tuple[FindingExpectation, ...]
    proposed_scores: ReviewScores | None
    expected_score_consistent: StrictBool | None


class QualityCorpus(_StrictModel):
    """Versioned synthetic quality-case corpus."""

    schema_version: Literal["1.0"]
    provenance: Literal["synthetic_controlled_fixture"]
    cases: tuple[QualityCase, ...]


@dataclass(frozen=True, slots=True)
class QualityCaseInputs:
    """Typed domain inputs constructed without consulting expectations."""

    case: QualityCase
    blocks: tuple[PdfBlock, ...]
    claim: PaperClaim
    candidates: tuple[ReviewFinding, ...]


def _candidate_status(judgment: JudgmentType) -> FindingStatus:
    match judgment:
        case JudgmentType.SUBJECTIVE:
            return FindingStatus.SUBJECTIVE_DIVERGENCE
        case JudgmentType.OBJECTIVE | JudgmentType.MIXED:
            return FindingStatus.CANDIDATE
        case _:
            assert_never(judgment)


def load_quality_corpus() -> QualityCorpus:
    """Parse the versioned synthetic corpus at the filesystem boundary."""
    return QualityCorpus.model_validate_json(_CORPUS_PATH.read_text(encoding="utf-8"))


def build_quality_case(case: QualityCase) -> QualityCaseInputs:
    """Convert one fixture case into evidence-gate domain inputs."""
    evidence_by_id = {item.evidence_id: item for item in case.page_evidence}
    blocks = tuple(
        PdfBlock(
            block_id=item.evidence_id,
            page=item.page,
            text=item.text,
            locator=item.locator,
        )
        for item in case.page_evidence
    )
    claim = PaperClaim(
        claim_id=f"{case.case_id}-C1",
        statement="The controlled paper fixture advances its stated empirical claim.",
        importance=ClaimImportance.CENTRAL,
        claim_type=ClaimType.EMPIRICAL,
    )
    candidates: list[ReviewFinding] = []
    for candidate in case.candidate_findings:
        locators = tuple(
            EvidenceLocator(
                page=evidence_by_id[evidence_id].page,
                locator=evidence_by_id[evidence_id].locator,
                summary=evidence_by_id[evidence_id].text,
                block_id=evidence_id,
            )
            for evidence_id in candidate.evidence_ids
        )
        candidates.extend(
            ReviewFinding(
                finding_id=(
                    candidate.finding_id
                    if reviewer_index == 0
                    else f"{candidate.finding_id}-R{reviewer_index + 1}"
                ),
                reviewer=f"controlled-perspective-{reviewer_index + 1}",
                category="significance" if case.proposed_scores else "soundness",
                judgment_type=candidate.judgment_type,
                severity=candidate.severity,
                status=_candidate_status(candidate.judgment_type),
                statement=candidate.statement,
                target_claim_id=claim.claim_id,
                evidence=locators,
                central_claim_impact=CentralClaimImpact.DIRECT,
                decision_relevance=DecisionRelevance.HIGH,
                recommended_check="Clarify the cited evidence and its claim impact.",
                confidence=0.8,
            )
            for reviewer_index in range(candidate.reviewer_count)
        )
    return QualityCaseInputs(
        case=case,
        blocks=blocks,
        claim=claim,
        candidates=tuple(candidates),
    )
