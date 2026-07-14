"""Live execution fails paper-locally without changing reviewer methodology."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from shutil import copyfile
from time import monotonic
from typing import TYPE_CHECKING, Final

import anyio
import pytest

from reviewharness.api_adapter import (
    AgentCredential,
    EventAssignment,
    IdempotencyKey,
    SubmissionReceipt,
)
from reviewharness.artifacts import ArtifactStore
from reviewharness.kernel import KernelReviewError
from reviewharness.live import (
    LiveExecutionContext,
    LivePreparation,
    classify_live_failure,
    execute_live_reviews,
)
from reviewharness.live_support import (
    LiveFailureKind,
    LivePaperStatus,
    LiveProvider,
    LiveRunConfig,
)
from reviewharness.schemas import ReviewSubmission, TrustedAssignment
from tests.unit.live_runbook_support import assignment, credential, guidance

FIXTURE: Final = Path(__file__).parents[1] / "fixtures" / "clean" / "sample.pdf"

if TYPE_CHECKING:
    from reviewharness.deadline import ReviewMode


def _review(paper_id: str) -> ReviewSubmission:
    return ReviewSubmission(
        paper_id=paper_id,
        soundness=3,
        presentation=3,
        significance=3,
        originality=3,
        overall_recommendation=4,
        confidence=3,
        comment=(
            "The paper presents a scoped empirical contribution with a clear "
            "evaluation target. The authors should retain the controlled comparison "
            "and clarify the limits of the supplied evidence in the final version."
        ),
    )


@dataclass(slots=True)
class _RecordingAdapter:
    submitted: list[int] = field(default_factory=list)
    idempotency_keys: list[IdempotencyKey] = field(default_factory=list)

    async def download_pdf(
        self,
        credential: AgentCredential,
        assignment: EventAssignment,
        destination: Path,
    ) -> Path:
        _ = (credential, assignment)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = copyfile(FIXTURE, destination)
        return destination

    async def submit_review(
        self,
        credential: AgentCredential,
        assignment: EventAssignment,
        review: ReviewSubmission,
        idempotency_key: IdempotencyKey | None = None,
    ) -> SubmissionReceipt:
        _ = (credential, review)
        assert idempotency_key is not None
        self.submitted.append(assignment.ordinal)
        self.idempotency_keys.append(idempotency_key)
        submitted = len(self.submitted)
        return SubmissionReceipt.model_validate(
            {
                "review_note_id": f"note-{assignment.ordinal}",
                "forum": f"forum-{assignment.ordinal}",
                "is_first_agent_review": True,
                "submitted": submitted,
                "remaining": 10 - submitted,
                "guidance": guidance("review_submitted", "submit_review"),
            }
        )


@dataclass(slots=True)
class _ScriptedKernel:
    calls: dict[int, int] = field(default_factory=dict)

    async def review(
        self,
        assignment: TrustedAssignment,
        mode: ReviewMode,
        output_dir: Path,
        shared_limiter: anyio.CapacityLimiter | None = None,
    ) -> ReviewSubmission:
        _ = (mode, output_dir, shared_limiter)
        ordinal = assignment.ordinal
        assert ordinal is not None
        call_number = self.calls.get(ordinal, 0) + 1
        self.calls[ordinal] = call_number
        if ordinal == 1 and call_number == 1:
            raise KernelReviewError(
                assignment.paper_id,
                "transient_provider_failure",
            )
        if ordinal == 2:
            raise KernelReviewError(
                assignment.paper_id,
                "evidence_contract_failure",
            )
        return _review(assignment.paper_id)


def test_transient_retry_succeeds_while_failed_sibling_gets_no_receipt(
    tmp_path: Path,
) -> None:
    # Given: one transient provider failure and one terminal evidence failure.
    adapter = _RecordingAdapter()
    kernel = _ScriptedKernel()
    store = ArtifactStore(tmp_path / "live")
    assignments = tuple(
        EventAssignment.model_validate(assignment(ordinal)) for ordinal in (1, 2)
    )
    context = LiveExecutionContext(
        preparation=LivePreparation(
            credential=credential(),
            assignments=assignments,
            deadline_seconds=60.0,
        ),
        config=LiveRunConfig(
            provider=LiveProvider.CODEX_EXEC,
            paper_concurrency=2,
            deadline_seconds=60.0,
            output_dir=store.root,
        ),
        store=store,
        started_at=monotonic(),
    )

    # When: both papers execute in the same bounded live batch.
    summary = anyio.run(execute_live_reviews, adapter, kernel, context)

    # Then: one exact retry is allowed, methodology is never substituted, and the
    # terminal sibling cannot create a submission receipt.
    first, second = summary.items
    assert first.status is LivePaperStatus.SUBMITTED
    assert first.retries == 1
    assert first.receipt_verified is True
    assert second.status is LivePaperStatus.FAILED
    assert second.failure is LiveFailureKind.EVIDENCE_CONTRACT
    assert second.retries == 0
    assert adapter.submitted == [1]
    assert kernel.calls == {1: 2, 2: 1}
    assert (store.paper_directory("ordinal-01") / "submission_receipt.json").exists()
    assert not (
        store.paper_directory("ordinal-02") / "submission_receipt.json"
    ).exists()
    completion_lines = store.completion_path.read_text(encoding="utf-8").splitlines()
    assert len(completion_lines) == 2
    assert any("evidence_contract_failure" in line for line in completion_lines)


@pytest.mark.parametrize(
    ("failure_kind", "expected"),
    [
        ("provider_failure", LiveFailureKind.PROVIDER),
        ("transient_provider_failure", LiveFailureKind.PROVIDER),
        ("evidence_contract_failure", LiveFailureKind.EVIDENCE_CONTRACT),
        ("unreviewable_empty_claim_ledger", LiveFailureKind.EVIDENCE_CONTRACT),
        ("score_provenance_failure", LiveFailureKind.SCORE_PROVENANCE),
        ("semantic_validation_failure", LiveFailureKind.SEMANTIC_VALIDATION),
    ],
)
def test_kernel_failure_categories_remain_typed_at_live_boundary(
    failure_kind: str,
    expected: LiveFailureKind,
) -> None:
    error = KernelReviewError("ordinal-01", failure_kind)

    assert classify_live_failure(error, "review") is expected
