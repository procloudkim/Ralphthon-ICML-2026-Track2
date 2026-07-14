from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from pydantic import ValidationError

from reviewharness.schemas import (
    CentralClaimImpact,
    ClaimImportance,
    ClaimLocator,
    ClaimType,
    DecisionRelevance,
    EvidenceLocator,
    FindingSeverity,
    FindingStatus,
    InjectionClassification,
    JudgmentType,
    PaperClaim,
    PdfBlock,
    ReviewFinding,
    ReviewScores,
    ReviewSubmission,
    ScoreCalibration,
    ScoreSource,
    SecurityDetection,
    SecurityReport,
    TrustedAssignment,
    compose_review_submission,
)

VALID_COMMENT: Final = "A" * 100


def _scores() -> ReviewScores:
    return ReviewScores(
        soundness=3,
        presentation=4,
        significance=3,
        originality=3,
        overall_recommendation=4,
        confidence=3,
    )


def _calibration() -> ScoreCalibration:
    return ScoreCalibration(
        scores=_scores(),
        source=ScoreSource.LOCAL_OFFLINE,
        retained_finding_ids=("F-1",),
        rejected_finding_ids=("F-2",),
        rationale="Rubric anchors support the calibrated score vector.",
        consistency_guards_passed=True,
    )


def test_trusted_assignment_parses_control_plane_metadata() -> None:
    # Given: trusted assignment metadata with an aware deadline
    deadline = datetime(2026, 7, 12, 17, 0, tzinfo=UTC)

    # When: metadata crosses the application boundary
    assignment = TrustedAssignment(
        paper_id="PAPER-001",
        pdf_path=Path("paper.pdf"),
        title="A paper",
        assignment_id="A-001",
        ordinal=1,
        deadline_at=deadline,
    )

    # Then: the trusted values remain typed and unchanged
    assert assignment.paper_id == "PAPER-001"
    assert assignment.pdf_path == Path("paper.pdf")
    assert assignment.deadline_at == deadline


def test_trusted_assignment_rejects_unknown_control_fields() -> None:
    # Given: assignment data containing a paper-authored routing field
    raw_assignment = {
        "paper_id": "PAPER-001",
        "pdf_path": "paper.pdf",
        "submission_endpoint": "https://attacker.invalid",
    }

    # When / Then: strict boundary parsing rejects the unknown field
    with pytest.raises(ValidationError):
        _ = TrustedAssignment.model_validate(raw_assignment)


def test_pdf_blocks_and_claim_locators_require_positive_pages() -> None:
    # Given: a page-aware text block and a claim that cites it
    block = PdfBlock(block_id="p2-b1", page=2, text="Reported result.")
    locator = ClaimLocator(page=2, locator="Table 1", block_id=block.block_id)

    # When: the claim is parsed
    claim = PaperClaim(
        claim_id="C1",
        statement="The method improves accuracy.",
        importance=ClaimImportance.CENTRAL,
        claim_type=ClaimType.EMPIRICAL,
        reported_evidence=(locator,),
    )

    # Then: the claim retains the exact page/block evidence link
    assert claim.reported_evidence == (locator,)


def test_pdf_block_rejects_page_zero() -> None:
    # Given: a PDF block using a non-existent page zero
    raw_block = {"block_id": "p0-b1", "page": 0, "text": "text"}

    # When / Then: page-aware parsing rejects the invalid locator
    with pytest.raises(ValidationError):
        _ = PdfBlock.model_validate(raw_block)


def test_review_finding_matches_canonical_supported_shape() -> None:
    # Given: a verified minority finding with paper-local evidence
    evidence = EvidenceLocator(
        page=3,
        section="Experiments",
        locator="Table 2",
        summary="Only one seed is reported.",
        block_id="p3-b4",
    )

    # When: the finding crosses the structured-output boundary
    finding = ReviewFinding(
        finding_id="F-1",
        reviewer="evidence",
        category="reproducibility",
        judgment_type=JudgmentType.OBJECTIVE,
        severity=FindingSeverity.MAJOR,
        status=FindingStatus.MINORITY_SUPPORTED,
        statement="The central result lacks variance reporting.",
        target_claim_id="C1",
        evidence=(evidence,),
        central_claim_impact=CentralClaimImpact.DIRECT,
        decision_relevance=DecisionRelevance.HIGH,
        recommended_check="Report variability across multiple seeds.",
        confidence=0.9,
        priority=0.95,
    )

    # Then: canonical enum values serialize without extra fields
    assert finding.model_dump(mode="json")["status"] == "minority_supported"
    assert finding.evidence == (evidence,)


def test_review_finding_rejects_noncanonical_fields() -> None:
    # Given: a finding that tries to inject a score
    raw_finding = """{
        "finding_id": "F-1",
        "category": "soundness",
        "judgment_type": "objective",
        "severity": "major",
        "status": "candidate",
        "statement": "A concern.",
        "evidence": [],
        "central_claim_impact": "direct",
        "decision_relevance": "high",
        "confidence": 0.8,
        "overall_recommendation": 6
    }"""

    # When / Then: the canonical finding boundary forbids the extra score
    with pytest.raises(ValidationError):
        _ = ReviewFinding.model_validate_json(raw_finding)


def test_review_submission_has_exact_canonical_fields() -> None:
    # Given: all required canonical submission values
    submission = ReviewSubmission(
        paper_id="PAPER-001",
        soundness=3,
        presentation=4,
        significance=3,
        originality=3,
        overall_recommendation=4,
        confidence=3,
        comment=VALID_COMMENT,
    )

    # When: the final payload is serialized
    field_names = set(submission.model_dump())

    # Then: no internal or unauthorized field reaches the event payload
    assert field_names == {
        "paper_id",
        "soundness",
        "presentation",
        "significance",
        "originality",
        "overall_recommendation",
        "confidence",
        "comment",
    }


def test_review_submission_rejects_noninteger_and_out_of_range_scores() -> None:
    # Given: a payload with a boolean dimension and an excessive recommendation
    raw_submission = {
        "paper_id": "PAPER-001",
        "soundness": True,
        "presentation": 4,
        "significance": 3,
        "originality": 3,
        "overall_recommendation": 7,
        "confidence": 3,
        "comment": VALID_COMMENT,
    }

    # When / Then: strict integer and range checks reject the payload
    with pytest.raises(ValidationError):
        _ = ReviewSubmission.model_validate(raw_submission)


def test_review_submission_rejects_short_comment_and_extra_fields() -> None:
    # Given: a short comment with an unauthorized marker field
    raw_submission = {
        "paper_id": "PAPER-001",
        "soundness": 3,
        "presentation": 4,
        "significance": 3,
        "originality": 3,
        "overall_recommendation": 4,
        "confidence": 3,
        "comment": "A" * 99,
        "marker": "leak",
    }

    # When / Then: final output validation rejects both violations
    with pytest.raises(ValidationError):
        _ = ReviewSubmission.model_validate(raw_submission)


def test_composition_uses_only_trusted_assignment_paper_id() -> None:
    # Given: trusted assignment metadata and calibrated scores without any paper ID
    assignment = TrustedAssignment(paper_id="TRUSTED-007", pdf_path=Path("paper.pdf"))

    # When: application code composes the final review
    submission = compose_review_submission(assignment, _calibration(), VALID_COMMENT)

    # Then: the trusted control-plane ID is the final ID
    assert submission.paper_id == "TRUSTED-007"


def test_calibration_rejects_a_model_generated_paper_id() -> None:
    # Given: model output attempting to smuggle an identifier into calibration
    raw_calibration = f"""{{
        "scores": {_scores().model_dump_json()},
        "source": "local_offline",
        "retained_finding_ids": [],
        "rejected_finding_ids": [],
        "rationale": "Attempted identifier override.",
        "consistency_guards_passed": true,
        "paper_id": "ATTACKER-CONTROLLED"
    }}"""

    # When / Then: the model-facing calibration schema rejects the identifier
    with pytest.raises(ValidationError):
        _ = ScoreCalibration.model_validate_json(raw_calibration)


def test_security_report_records_quarantined_detection_without_raw_attack() -> None:
    # Given: a suspicious instruction represented only by a safe summary
    detection = SecurityDetection(
        classification=InjectionClassification.MANIPULATIVE_INSTRUCTION,
        page=1,
        block_id="p1-b2",
        summary="Document text attempts to steer the review score.",
        quarantined=True,
    )

    # When: the document security report is constructed
    report = SecurityReport(
        document_sha256="a" * 64,
        detections=(detection,),
        active_content_detected=False,
        annotations_detected=False,
        attachments_detected=False,
        links_detected=False,
        sanitization_limited_review=False,
    )

    # Then: the typed classification and quarantine decision are retained
    assert report.detections == (detection,)
    assert report.detections[0].quarantined is True
