"""Strict parsing and deterministic execution of synthetic security cases."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import ClassVar, Final, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field

from .injection import (
    InjectionScan,
    QuarantineResult,
    SourceOrigin,
    SourceSpan,
    quarantine_sources,
)
from .schemas import InjectionClassification
from .secure_ingest import ingest_pdf

_ROOT: Final = Path(__file__).resolve().parents[2]
_CASES: Final = _ROOT / "tests" / "fixtures" / "security" / "cases.json"
_SCIENCE: Final = "The controlled study evaluates a classifier on two datasets."


class _StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ExpectedAction(StrEnum):
    """Required containment action for a synthetic corpus case."""

    QUARANTINE = "quarantine"
    RETAIN = "retain_as_quoted_evidence"


class SecurityCase(_StrictModel):
    """One versioned security case parsed from the controlled corpus."""

    case_id: str
    attack_type: str
    fixture_path: str
    source_channel: SourceOrigin
    location: str
    untrusted_text: str
    expected_classification: InjectionClassification
    expected_action: ExpectedAction
    expected_scientific_score_delta: Literal[0]
    must_not_emit: tuple[str, ...]


class _SecurityCorpus(_StrictModel):
    schema_version: Literal["1.0"]
    provenance: Literal["synthetic_controlled_fixture"]
    cases: tuple[SecurityCase, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Measured containment outcome for one controlled security case."""

    case: SecurityCase
    quarantine: QuarantineResult
    detection_match: bool
    action_match: bool
    marker_safe: bool
    ingest_safe: bool


def is_attack(case: SecurityCase) -> bool:
    """Return whether a case requires quarantine as an attack."""
    match case.expected_action:
        case ExpectedAction.QUARANTINE:
            return True
        case ExpectedAction.RETAIN:
            return False
        case _:
            assert_never(case.expected_action)


def _scan(case: SecurityCase) -> InjectionScan:
    sources = (
        SourceSpan(1, "scientific", "page=1;block=0", _SCIENCE),
        SourceSpan(
            1,
            case.case_id,
            case.location,
            case.untrusted_text,
            case.source_channel,
        ),
    )
    digest = sha256(f"{case.case_id}|{case.untrusted_text}".encode()).hexdigest()
    return InjectionScan(document_sha256=digest, sources=sources)


def _action_matches(case: SecurityCase, result: QuarantineResult) -> bool:
    variants = (result.placeholder_variant, result.removal_variant)
    match case.expected_action:
        case ExpectedAction.QUARANTINE:
            return len(result.quarantined_spans) == 1 and all(
                case.untrusted_text not in span.text
                for variant in variants
                for span in variant
            )
        case ExpectedAction.RETAIN:
            return not result.quarantined_spans and all(
                any(span.text == case.untrusted_text for span in variant)
                for variant in variants
            )
        case _:
            assert_never(case.expected_action)


def evaluate_security_case(case: SecurityCase) -> CaseOutcome:
    """Evaluate one parsed case through quarantine and real PDF ingestion."""
    quarantine = quarantine_sources(_scan(case))
    ingested = ingest_pdf(_ROOT / case.fixture_path)
    benign = InjectionClassification.BENIGN_QUOTED_EXAMPLE
    ingest_safe = (
        not ingested.security.scientific_penalty_applied
        and all(
            finding.quarantined == (finding.category is not benign)
            for finding in ingested.security.findings
        )
        and "RH_" not in ingested.sanitized_text
    )
    safe_text = "\n".join(span.text for span in quarantine.placeholder_variant)
    return CaseOutcome(
        case=case,
        quarantine=quarantine,
        detection_match=any(
            detection.classification is case.expected_classification
            for detection in quarantine.report.detections
        ),
        action_match=_action_matches(case, quarantine),
        marker_safe=all(marker not in safe_text for marker in case.must_not_emit),
        ingest_safe=ingest_safe,
    )


def load_security_cases() -> tuple[SecurityCase, ...]:
    """Parse the versioned controlled corpus at the filesystem boundary."""
    return _SecurityCorpus.model_validate_json(_CASES.read_text(encoding="utf-8")).cases
