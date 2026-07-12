"""Deterministic evidence preparation and kernel artifact persistence."""

import json
import re
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

from pydantic import BaseModel, TypeAdapter

from reviewharness import artifacts, evidence, reviewers, schemas, secure_ingest
from reviewharness.deadline import ReviewMode
from reviewharness.providers import SanitizedEvidencePage, SanitizedPaperEvidence

_HEADING: Final = re.compile(
    r"^(?:\d+(?:\.\d+)*\s+)?(?:abstract|introduction|method|experiments?|results?|limitations?|conclusions?)\b",
    re.IGNORECASE,
)
_MAX_HEADING: Final = 80
_JSON: Final[TypeAdapter[artifacts.JsonValue]] = TypeAdapter[artifacts.JsonValue](
    artifacts.JsonValue
)
_EVENTS: Final = (
    "assignment_validated",
    "pdf_ingested",
    "evidence_sanitized",
    "claims_built",
    "reviewers_completed",
    "findings_resolved",
    "scores_calibrated",
    "submission_validated",
)


@dataclass(frozen=True, slots=True)
class PreparedEvidence:
    """Sanitized block and provider views from one paper only."""

    blocks: tuple[schemas.PdfBlock, ...]
    provider_evidence: SanitizedPaperEvidence


@dataclass(frozen=True, slots=True)
class ReviewerData:
    """Typed reviewer aggregates without majority-score reduction."""

    outputs: tuple[BaseModel, ...]
    claims: tuple[schemas.PaperClaim, ...]
    findings: tuple[schemas.ReviewFinding, ...]
    proposals: tuple[schemas.ScoreProposal, ...]
    failures: int


@dataclass(frozen=True, slots=True)
class KernelTrace:
    """Complete validated evidence needed for required paper artifacts."""

    mode: ReviewMode
    ingest: secure_ingest.SecureIngestResult
    prepared: PreparedEvidence
    reviewer_data: ReviewerData
    claims: tuple[schemas.PaperClaim, ...]
    resolution: evidence.EvidenceResolution
    calibration: schemas.ScoreCalibration
    submission: schemas.ReviewSubmission


def _model_json(model: BaseModel) -> artifacts.JsonValue:
    return _JSON.validate_json(model.model_dump_json())


def _models_json(models: Sequence[BaseModel]) -> list[artifacts.JsonValue]:
    return [_model_json(model) for model in models]


def prepare_evidence(
    ingest: secure_ingest.SecureIngestResult,
) -> PreparedEvidence:
    """Build page-aware blocks and prefixed provider text with attacks replaced."""
    redactions = {
        finding.location: finding.replacement
        for finding in ingest.security.findings
        if finding.source is secure_ingest.InstructionSource.TEXT
        and finding.quarantined
    }
    blocks: list[schemas.PdfBlock] = []
    pages: list[SanitizedEvidencePage] = []
    for page in ingest.document.pages:
        section: str | None = None
        safe_lines: list[str] = []
        for line in page.lines:
            locator = line.locator
            block_id = f"p{page.number}-b{locator.block_index}-l{locator.line_index}"
            location = (
                f"page={locator.page};block={locator.block_index};"
                f"line={locator.line_index}"
            )
            text = redactions.get(location, line.text).strip()
            if not text:
                continue
            if len(text) <= _MAX_HEADING and _HEADING.match(text) is not None:
                section = text
            blocks.append(
                schemas.PdfBlock(
                    block_id=block_id,
                    page=page.number,
                    text=text,
                    section=section,
                    locator=block_id,
                )
            )
            safe_lines.append(f"[{block_id}] {text}")
        pages.append(
            SanitizedEvidencePage(
                page_number=page.number,
                text="\n".join(safe_lines),
            )
        )
    notes = tuple(
        f"{finding.category.value}:{finding.finding_id}"
        for finding in ingest.security.findings
    )
    return PreparedEvidence(
        tuple(blocks),
        SanitizedPaperEvidence(
            document_sha256=ingest.document.sha256,
            pages=tuple(pages),
            security_notes=notes,
        ),
    )


def collect_reviewer_data(result: reviewers.ReviewerRunResult) -> ReviewerData:
    """Collect full or fast outputs while retaining every candidate finding."""
    outputs: list[BaseModel] = []
    paper_claims: list[schemas.PaperClaim] = []
    findings: list[schemas.ReviewFinding] = []
    proposals: list[schemas.ScoreProposal] = []
    failures = 0
    for outcome in result.outcomes:
        match outcome:
            case reviewers.ReviewerSuccess(
                output=reviewers.TriLensCandidates() as output
            ):
                outputs.append(output)
                paper_claims.extend(output.claims)
                findings.extend(output.findings)
                if output.score_proposal is not None:
                    proposals.append(output.score_proposal)
            case reviewers.ReviewerSuccess(
                output=reviewers.SpecialistCandidates() as output
            ):
                outputs.append(output)
                findings.extend(output.findings)
            case reviewers.ReviewerFailure():
                failures += 1
            case _:
                assert_never(outcome)
    return ReviewerData(
        tuple(outputs),
        tuple(paper_claims),
        tuple(findings),
        tuple(proposals),
        failures,
    )


def _write_events(
    store: artifacts.ArtifactStore,
    paper_id: str,
    mode: ReviewMode,
) -> None:
    text = "".join(
        json.dumps({"event": event, "mode": mode.value, "paper_id": paper_id}) + "\n"
        for event in _EVENTS
    )
    record = store.write_text(paper_id, "events", text)
    _ = record.path.replace(store.paper_directory(paper_id) / "events.jsonl")
    with suppress(FileNotFoundError):
        record.manifest_path.unlink()


def persist_trace(
    output_dir: Path,
    assignment: schemas.TrustedAssignment,
    trace: KernelTrace,
) -> None:
    """Atomically persist all required per-paper evidence artifacts."""
    store = artifacts.ArtifactStore(output_dir)
    payloads: tuple[tuple[str, artifacts.JsonValue], ...] = (
        ("assignment", _model_json(assignment)),
        (
            "pdf_hash",
            {
                "sha256": trace.ingest.document.sha256,
                "page_count": trace.ingest.document.page_count,
            },
        ),
        ("security_scan", _model_json(trace.ingest.security)),
        (
            "parsed_structure",
            {
                "page_count": trace.ingest.document.page_count,
                "blocks": _models_json(trace.prepared.blocks),
            },
        ),
        ("claim_ledger", _models_json(trace.claims)),
        (
            "reviewer_outputs",
            {
                "outputs": _models_json(trace.reviewer_data.outputs),
                "failure_count": trace.reviewer_data.failures,
            },
        ),
        ("normalized_findings", _models_json(trace.resolution.retained)),
        ("rejected_findings", _models_json(trace.resolution.rejected)),
        ("score_trace", _model_json(trace.calibration)),
        ("review", _model_json(trace.submission)),
    )
    for stage, payload in payloads:
        _ = store.write_json(assignment.paper_id, stage, payload)
    _write_events(store, assignment.paper_id, trace.mode)
