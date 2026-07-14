"""End-to-end contracts for provider-visible scientific evidence."""

from pathlib import Path
from typing import ClassVar, Final

import anyio
from pydantic import BaseModel, ConfigDict, TypeAdapter

from reviewharness.deadline import ReviewMode
from reviewharness.kernel import ReviewKernel
from reviewharness.kernel_support import prepare_evidence
from reviewharness.provider_contracts import ProviderContractStats
from reviewharness.providers import (
    ReviewerResponse,
    ScriptedReviewerProvider,
    ScriptedSuccess,
)
from reviewharness.schemas import PaperClaim, ReviewFinding, TrustedAssignment
from reviewharness.secure_ingest import ingest_pdf

FIXTURES: Final = Path(__file__).parents[1] / "fixtures"
CONFORMANCE_PDF: Final = FIXTURES / "conformance" / "paragraph_contract.pdf"
PROVIDER_OUTPUT: Final = FIXTURES / "provider_outputs" / "paragraph_contract.json"
CLAIMS: Final = TypeAdapter(tuple[PaperClaim, ...])
FINDINGS: Final = TypeAdapter(tuple[ReviewFinding, ...])


class _ReviewerOutputs(BaseModel):
    """Typed view of the persisted reviewer-output artifact."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    outputs: tuple[object, ...]
    failure_count: int
    provider_contract: ProviderContractStats


def test_provider_evidence_uses_parser_owned_paragraph_blocks() -> None:
    """A wrapped scientific paragraph is exposed under one exact block ID."""
    prepared = prepare_evidence(ingest_pdf(CONFORMANCE_PDF))
    central = next(
        block for block in prepared.blocks if "budget-aware classifier" in block.text
    )
    limitation = next(
        block for block in prepared.blocks if "one fixed seed" in block.text
    )

    assert central.block_id.startswith("p1-b")
    assert limitation.block_id.startswith("p1-b")
    assert "-l" not in central.block_id
    assert "-l" not in limitation.block_id
    assert "two public datasets" in central.text
    assert "reports no variance" in limitation.text
    assert (
        f"[{central.block_id}] {central.text}"
        in prepared.provider_evidence.pages[0].text
    )


def test_provider_replay_preserves_claim_finding_and_comment(tmp_path: Path) -> None:
    """A valid provider signal survives the complete fast-mode kernel."""
    raw_output = PROVIDER_OUTPUT.read_text(encoding="utf-8")
    provider = ScriptedReviewerProvider(
        (ScriptedSuccess(ReviewerResponse(raw_output=raw_output)),)
    )
    assignment = TrustedAssignment(
        paper_id="CONFORMANCE-001",
        pdf_path=CONFORMANCE_PDF,
    )

    review = anyio.run(
        ReviewKernel(provider).review,
        assignment,
        ReviewMode.FAST,
        tmp_path,
    )
    paper_dir = tmp_path / assignment.paper_id
    ledger = CLAIMS.validate_json((paper_dir / "claim_ledger.json").read_text("utf-8"))
    findings = FINDINGS.validate_json(
        (paper_dir / "normalized_findings.json").read_text("utf-8")
    )
    outputs = _ReviewerOutputs.model_validate_json(
        (paper_dir / "reviewer_outputs.json").read_text("utf-8")
    )

    assert ledger[0].importance == "central"
    assert ledger[0].reported_evidence[0].block_id == "p1-b2"
    assert findings[0].finding_id == "F1"
    assert findings[0].evidence[0].block_id == "p1-b4"
    assert outputs.provider_contract == ProviderContractStats(
        claim_candidates=1,
        accepted_claims=1,
        evidence_candidates=1,
        accepted_evidence=1,
        finding_candidates=1,
    )
    assert "uses one fixed seed" in review.comment
    assert "No evidence-located retained concern" not in review.comment
