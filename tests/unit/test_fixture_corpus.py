from pathlib import Path
from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures"
CORPUS_PATHS = (
    FIXTURE_ROOT / "batch" / "assignments.json",
    FIXTURE_ROOT / "security" / "cases.json",
    FIXTURE_ROOT / "quality" / "cases.json",
)
REQUIRED_ATTACKS: Final = frozenset(
    {
        "direct_score_steering",
        "omit_weaknesses",
        "fake_authority",
        "fake_rubric",
        "marker_canary",
        "json_breakout",
        "secret_request",
        "shell_or_url",
        "cross_paper_poisoning",
        "hidden_text",
        "metadata_injection",
        "benign_quoted_attack",
    }
)
REQUIRED_QUALITY_CASES: Final = frozenset(
    {
        "supported_minority_central",
        "unsupported_factual_major",
        "objective_consensus",
        "contested_subjective",
        "score_consistency",
    }
)


class CorpusModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class BatchAssignment(CorpusModel):
    paper_id: str = Field(pattern=r"^SAMPLE-\d{3}$")
    pdf_path: str
    requested_mode: Literal["full", "fast"]


class BatchCorpus(CorpusModel):
    schema_version: Literal["1.0"]
    provenance: Literal["synthetic_controlled_fixture"]
    assignments: tuple[BatchAssignment, ...]


class SecurityCase(CorpusModel):
    case_id: str
    attack_type: Literal[
        "direct_score_steering",
        "omit_weaknesses",
        "fake_authority",
        "fake_rubric",
        "marker_canary",
        "json_breakout",
        "secret_request",
        "shell_or_url",
        "cross_paper_poisoning",
        "hidden_text",
        "metadata_injection",
        "benign_quoted_attack",
    ]
    fixture_path: str
    source_channel: Literal["visible_text", "hidden_text", "metadata"]
    location: str
    untrusted_text: str
    expected_classification: Literal[
        "manipulative_instruction",
        "reviewer_detection_canary",
        "benign_quoted_example",
        "uncertain_instruction",
    ]
    expected_action: Literal["quarantine", "retain_as_quoted_evidence"]
    expected_scientific_score_delta: Literal[0]
    must_not_emit: tuple[str, ...]


class SecurityCorpus(CorpusModel):
    schema_version: Literal["1.0"]
    provenance: Literal["synthetic_controlled_fixture"]
    cases: tuple[SecurityCase, ...]


class PageEvidence(CorpusModel):
    evidence_id: str
    page: int = Field(ge=1)
    locator: str
    text: str


class CandidateFinding(CorpusModel):
    finding_id: str
    reviewer_count: int = Field(ge=1, le=3)
    judgment_type: Literal["objective", "subjective", "mixed"]
    severity: Literal["critical", "major", "minor", "observation"]
    statement: str
    evidence_ids: tuple[str, ...]


class FindingExpectation(CorpusModel):
    finding_id: str
    expected_action: Literal["retain", "reject"]
    expected_status: Literal[
        "consensus_supported",
        "minority_supported",
        "contested",
        "unsupported_rejected",
        "subjective_divergence",
    ]
    reason: str


class ProposedScores(CorpusModel):
    soundness: int = Field(ge=1, le=4)
    presentation: int = Field(ge=1, le=4)
    significance: int = Field(ge=1, le=4)
    originality: int = Field(ge=1, le=4)
    overall_recommendation: int = Field(ge=1, le=6)
    confidence: int = Field(ge=1, le=5)


class QualityCase(CorpusModel):
    case_id: str
    case_type: Literal[
        "supported_minority_central",
        "unsupported_factual_major",
        "objective_consensus",
        "contested_subjective",
        "score_consistency",
    ]
    paper_path: str
    page_evidence: tuple[PageEvidence, ...]
    candidate_findings: tuple[CandidateFinding, ...]
    expectations: tuple[FindingExpectation, ...]
    proposed_scores: ProposedScores | None
    expected_score_consistent: bool | None


class QualityCorpus(CorpusModel):
    schema_version: Literal["1.0"]
    provenance: Literal["synthetic_controlled_fixture"]
    cases: tuple[QualityCase, ...]


def test_batch_corpus_has_ten_unique_trusted_assignments() -> None:
    # Given
    corpus = BatchCorpus.model_validate_json(CORPUS_PATHS[0].read_text("utf-8"))
    expected_ids = tuple(f"SAMPLE-{index:03d}" for index in range(1, 11))

    # When
    assignments = corpus.assignments

    # Then
    assert len(assignments) == 10
    assert tuple(assignment.paper_id for assignment in assignments) == expected_ids
    assert {assignment.requested_mode for assignment in assignments} == {"full", "fast"}
    assert all(
        not Path(assignment.pdf_path).is_absolute() for assignment in assignments
    )
    assert all(
        (REPOSITORY_ROOT / assignment.pdf_path).is_file() for assignment in assignments
    )


def test_security_corpus_covers_every_required_control() -> None:
    # Given
    corpus = SecurityCorpus.model_validate_json(CORPUS_PATHS[1].read_text("utf-8"))
    # When
    cases = corpus.cases

    # Then
    assert {case.attack_type for case in cases} == REQUIRED_ATTACKS
    assert len({case.case_id for case in cases}) == len(cases)
    assert all((REPOSITORY_ROOT / case.fixture_path).is_file() for case in cases)
    assert all(case.expected_scientific_score_delta == 0 for case in cases)
    assert all(case.location and case.untrusted_text for case in cases)
    canary = next(case for case in cases if case.attack_type == "marker_canary")
    assert canary.must_not_emit == ("RH_CANARY_DO_NOT_EMIT_7F3A",)
    benign = next(case for case in cases if case.attack_type == "benign_quoted_attack")
    assert benign.expected_classification == "benign_quoted_example"
    assert benign.expected_action == "retain_as_quoted_evidence"


def test_quality_corpus_has_page_local_complete_expectations() -> None:
    # Given
    corpus = QualityCorpus.model_validate_json(CORPUS_PATHS[2].read_text("utf-8"))
    # When
    cases = corpus.cases

    # Then
    assert {case.case_type for case in cases} == REQUIRED_QUALITY_CASES
    assert len({case.case_id for case in cases}) == len(cases)
    assert all((REPOSITORY_ROOT / case.paper_path).is_file() for case in cases)
    for case in cases:
        evidence_ids = {evidence.evidence_id for evidence in case.page_evidence}
        candidate_ids = {finding.finding_id for finding in case.candidate_findings}
        expected_ids = {expectation.finding_id for expectation in case.expectations}
        assert evidence_ids
        assert all(
            evidence.locator and evidence.text for evidence in case.page_evidence
        )
        assert all(
            set(finding.evidence_ids) <= evidence_ids
            for finding in case.candidate_findings
        )
        assert candidate_ids == expected_ids
    score_case = next(case for case in cases if case.case_type == "score_consistency")
    assert score_case.proposed_scores is not None
    assert score_case.expected_score_consistent is False


def test_corpora_make_no_human_label_or_correlation_claim() -> None:
    # Given
    forbidden_claims = (
        "human_ground_truth",
        "human_label",
        "human score",
        "pearson",
        "spearman",
        "correlation",
    )

    # When
    corpus_text = "\n".join(path.read_text("utf-8").lower() for path in CORPUS_PATHS)

    # Then
    assert not any(claim in corpus_text for claim in forbidden_claims)
