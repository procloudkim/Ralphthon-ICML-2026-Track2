from pathlib import Path
from threading import Event, Thread
from typing import Final

import anyio
import pytest
from anyio.to_thread import run_sync
from pydantic import TypeAdapter

import reviewharness.kernel_support as support
from reviewharness import secure_ingest
from reviewharness.deadline import ReviewMode
from reviewharness.kernel import KernelReviewError, ReviewKernel
from reviewharness.schemas import (
    CentralClaimImpact,
    ClaimImportance,
    ClaimType,
    DecisionRelevance,
    FindingSeverity,
    FindingStatus,
    JudgmentType,
    PaperClaim,
    ReviewFinding,
    ReviewSubmission,
    TrustedAssignment,
)

FIXTURES: Final = Path(__file__).parents[1] / "fixtures"
CLEAN_PDF: Final = FIXTURES / "clean" / "sample.pdf"
INJECTED_PDF: Final = FIXTURES / "injected" / "direct_score_steering.pdf"
REQUIRED_ARTIFACTS: Final = {
    "assignment.json",
    "pdf_hash.json",
    "security_scan.json",
    "parsed_structure.json",
    "claim_ledger.json",
    "reviewer_outputs.json",
    "normalized_findings.json",
    "rejected_findings.json",
    "score_trace.json",
    "comment_trace.json",
    "review.json",
    "events.jsonl",
}
CLAIMS_ADAPTER: Final = TypeAdapter(tuple[PaperClaim, ...])
FINDINGS_ADAPTER: Final = TypeAdapter(tuple[ReviewFinding, ...])


def _review(
    kernel: ReviewKernel,
    assignment: TrustedAssignment,
    mode: ReviewMode,
    output_dir: Path,
) -> ReviewSubmission:
    return anyio.run(kernel.review, assignment, mode, output_dir)


def _assert_findings_reference_ledger(paper_dir: Path) -> None:
    claims = CLAIMS_ADAPTER.validate_json(
        (paper_dir / "claim_ledger.json").read_text(encoding="utf-8")
    )
    findings = FINDINGS_ADAPTER.validate_json(
        (paper_dir / "normalized_findings.json").read_text(encoding="utf-8")
    )
    claim_ids = {claim.claim_id for claim in claims}
    major_findings = tuple(
        finding for finding in findings if finding.severity is FindingSeverity.MAJOR
    )

    assert all(
        finding.target_claim_id is None or finding.target_claim_id in claim_ids
        for finding in findings
    )
    assert major_findings
    assert all(
        (
            finding.target_claim_id is None
            and finding.central_claim_impact is CentralClaimImpact.UNCERTAIN
        )
        or (
            finding.target_claim_id in claim_ids
            and finding.central_claim_impact
            in {CentralClaimImpact.DIRECT, CentralClaimImpact.INDIRECT}
        )
        for finding in major_findings
    )


@pytest.mark.parametrize(
    ("central_count", "provider_target"),
    [(1, "C-RAW-MISSING"), (1, "C-CENTRAL-0"), (2, "C-RAW-MISSING")],
)
def test_dangling_finding_target_is_fail_closed(
    central_count: int,
    provider_target: str,
) -> None:
    # Given: an unresolved provider target and one or multiple central claims.
    ledger = tuple(
        PaperClaim(
            claim_id=f"C-CENTRAL-{index}",
            statement=f"Central claim {index}",
            importance=ClaimImportance.CENTRAL,
            claim_type=ClaimType.OTHER,
        )
        for index in range(central_count)
    )
    finding = ReviewFinding(
        finding_id="F-DANGLING",
        category="scope",
        judgment_type=JudgmentType.MIXED,
        severity=FindingSeverity.MAJOR,
        status=FindingStatus.CANDIDATE,
        statement="A scoped limitation targets an unavailable provider claim.",
        target_claim_id=provider_target,
        evidence=(),
        central_claim_impact=CentralClaimImpact.UNCERTAIN,
        decision_relevance=DecisionRelevance.HIGH,
        confidence=0.8,
    )

    # When: provider targets are relinked against the normalized ledger.
    linked = support.relink_findings((finding,), (), ledger)

    # Then: no provider-free guess or fallback can create central-claim impact.
    assert linked[0].target_claim_id is None


def test_clean_paper_full_mode_is_valid_traced_and_deterministic(
    tmp_path: Path,
) -> None:
    # Given: trusted metadata and the deterministic tool-less local provider.
    assignment = TrustedAssignment(
        paper_id="SAMPLE-001",
        pdf_path=CLEAN_PDF,
        title="Controlled ML Study",
    )
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    # When: the isolated full kernel reviews identical paper bytes twice.
    first = _review(ReviewKernel(), assignment, ReviewMode.FULL, first_dir)
    second = _review(ReviewKernel(), assignment, ReviewMode.FULL, second_dir)

    # Then: output, trusted ID, and every required evidence artifact are stable.
    assert first == second
    assert first.paper_id == "SAMPLE-001"
    assert ReviewSubmission.model_validate(first.model_dump()) == first
    first_paper_dir = first_dir / assignment.paper_id
    second_paper_dir = second_dir / assignment.paper_id
    assert {
        path.name for path in first_paper_dir.iterdir() if path.is_file()
    } >= REQUIRED_ARTIFACTS
    assert (
        ReviewSubmission.model_validate_json(
            (first_paper_dir / "review.json").read_text(encoding="utf-8")
        )
        == first
    )
    assert (first_paper_dir / "score_trace.json").read_bytes() == (
        second_paper_dir / "score_trace.json"
    ).read_bytes()
    assert (first_paper_dir / "normalized_findings.json").read_bytes() == (
        second_paper_dir / "normalized_findings.json"
    ).read_bytes()
    _assert_findings_reference_ledger(first_paper_dir)


def test_clean_paper_fast_mode_preserves_supported_minority_only(
    tmp_path: Path,
) -> None:
    # Given: a clean empirical fixture with paper-local limitations evidence.
    assignment = TrustedAssignment(
        paper_id="SAMPLE-FAST",
        pdf_path=CLEAN_PDF,
    )

    # When: one tri-lens provider call executes the fast kernel path.
    result = _review(ReviewKernel(), assignment, ReviewMode.FAST, tmp_path)

    # Then: the result is valid and unsupported major criticism cannot ship.
    paper_dir = tmp_path / assignment.paper_id
    retained_text = (paper_dir / "normalized_findings.json").read_text(encoding="utf-8")
    assert result.paper_id == assignment.paper_id
    assert "unsupported_rejected" not in retained_text
    assert "minority_supported" in retained_text
    _assert_findings_reference_ledger(paper_dir)


def test_injected_paper_cannot_steer_score_identifier_or_comment(
    tmp_path: Path,
) -> None:
    # Given: an adversarial PDF requesting score 6, omission, and marker leakage.
    assignment = TrustedAssignment(
        paper_id="TRUSTED-INJECTED-001",
        pdf_path=INJECTED_PDF,
    )

    # When: sanitization leaves no canonical scientific claim.
    with pytest.raises(KernelReviewError) as raised:
        _ = _review(ReviewKernel(), assignment, ReviewMode.FULL, tmp_path)

    # Then: the kernel fails closed instead of fabricating a generic submission.
    assert raised.value.failure_kind == "unreviewable_empty_claim_ledger"
    paper_dir = tmp_path / assignment.paper_id
    assert not (paper_dir / "review.json").exists()


def test_kernel_cancellation_abandons_blocking_secure_ingest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: secure ingest is running in a worker that cannot finish on its own.
    started = Event()
    release = Event()
    review_finished = Event()
    finished_before_release = Event()
    ingest_finished = Event()
    original_ingest = secure_ingest.ingest_pdf

    def blocking_ingest(path: Path) -> secure_ingest.SecureIngestResult:
        started.set()
        _ = release.wait()
        try:
            return original_ingest(path)
        finally:
            ingest_finished.set()

    def release_watchdog() -> None:
        if review_finished.wait(timeout=1.0):
            finished_before_release.set()
        release.set()

    monkeypatch.setattr(secure_ingest, "ingest_pdf", blocking_ingest)
    assignment = TrustedAssignment(
        paper_id="SAMPLE-CANCEL",
        pdf_path=CLEAN_PDF,
    )

    async def cancel_running_review() -> None:
        async def run_review() -> None:
            try:
                _ = await ReviewKernel().review(
                    assignment,
                    ReviewMode.FAST,
                    tmp_path,
                )
            finally:
                review_finished.set()

        async with anyio.create_task_group() as task_group:
            _ = task_group.start_soon(run_review)
            _ = await run_sync(
                started.wait,
                abandon_on_cancel=True,
            )
            task_group.cancel_scope.cancel()

    # When: structured cancellation reaches the review while ingest is blocked.
    watchdog = Thread(target=release_watchdog)
    watchdog.start()
    anyio.run(cancel_running_review)
    watchdog.join(timeout=5.0)

    # Then: the review task exits before the worker is allowed to finish.
    assert not watchdog.is_alive()
    assert finished_before_release.is_set()
    assert ingest_finished.wait(timeout=5.0)
