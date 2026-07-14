"""Strict evaluator-artifact boundary for anonymous report generation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal, Self, override

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    ValidationError,
    model_validator,
)
from pydantic_core import PydanticCustomError

type _Count = Annotated[int, Field(strict=True, ge=0)]
type _PositiveCount = Annotated[int, Field(strict=True, ge=1)]
type _Rate = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]
type _NonNegativeMetric = Annotated[FiniteFloat, Field(ge=0.0)]
_EXPECTED_PAGE_COUNT: Final = 4


class _StrictMetrics(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class _SecurityMetrics(_StrictMetrics):
    evaluated_cases: _PositiveCount
    attack_success_rate: _Rate
    marker_leakage_rate: _Rate
    unauthorized_tool_calls: _Count | None
    unauthorized_tool_calls_status: Literal["unmeasured_no_instrumented_runner"]
    trusted_id_invariance_rate: _Rate
    valid_completion_rate: _Rate
    benign_false_positive_rate: _Rate
    duration_seconds: _NonNegativeMetric
    detection_recall: _Rate
    clean_injected_score_delta: _NonNegativeMetric
    clean_injected_issue_overlap: _Rate
    paired_document_count: _PositiveCount
    evaluation_scope: Literal[
        "synthetic_attack_cases_plus_public_paired_documents_local_provider"
    ]
    provider_scope: Literal["local_heuristic_no_tools_no_network"]
    passed: bool


class _QualityMetrics(_StrictMetrics):
    evaluated_cases: _PositiveCount
    evidence_coverage: _Rate | None
    unsupported_critique_rate: _Rate | None
    issue_precision: _Rate | None
    issue_recall: _Rate | None
    minority_preservation_rate: _Rate | None
    score_comment_consistency_rate: _Rate | None
    valid_completion_rate: _Rate | None
    repeatability_rate: _Rate | None
    top_issue_stability_rate: _Rate | None
    provider_conformance_passed: bool
    evaluation_scope: Literal["synthetic_component_cases_plus_public_provider_replay"]
    duration_seconds: _NonNegativeMetric
    passed: bool
    human_correlation: None
    human_correlation_unavailable_reason: Literal[
        "human labels and private judge heuristics were unavailable during development"
    ]

    @model_validator(mode="after")
    def _validate_passed_gate(self) -> Self:
        required_rates = (
            self.evidence_coverage,
            self.unsupported_critique_rate,
            self.issue_precision,
            self.issue_recall,
            self.minority_preservation_rate,
            self.score_comment_consistency_rate,
            self.valid_completion_rate,
            self.repeatability_rate,
            self.top_issue_stability_rate,
        )
        if self.passed and (
            any(value is None for value in required_rates)
            or not self.provider_conformance_passed
        ):
            code = "unproven_quality_gate"
            message = "passed requires every quality metric and provider conformance"
            raise PydanticCustomError(code, message)
        return self


class _RuntimeMetrics(_StrictMetrics):
    paper_count: _PositiveCount
    distinct_pdf_count: _PositiveCount
    valid_completion_count: _Count
    total_seconds: _NonNegativeMetric
    p50_seconds: _NonNegativeMetric
    p95_seconds: _NonNegativeMetric
    timeout_count: _Count
    retry_count: _Count
    fast_mode_fallback_count: _Count
    invalid_output_count: _Count
    failure_isolation_passed: bool
    full_mode_executed: bool
    fast_mode_executed: bool
    monotonic_deadline: bool
    evaluation_scope: Literal["local_synthetic_hash_distinct_pdf_batch"]
    provider_scope: Literal["local_heuristic_no_network"]
    real_provider_ten_paper_runtime_status: Literal["unverified"]
    real_provider_ten_paper_runtime_seconds: None


@dataclass(frozen=True, slots=True)
class ReportMetrics:
    """Validated aggregate metrics used by the report renderer."""

    security: _SecurityMetrics
    quality: _QualityMetrics
    runtime: _RuntimeMetrics


@dataclass(frozen=True, slots=True)
class ReportSection:
    """One heading and its report paragraphs."""

    heading: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StaticReportContent:
    """Validated static prose sections for each report page."""

    problem: tuple[ReportSection, ...]
    method: tuple[ReportSection, ...]
    protocol: tuple[ReportSection, ...]
    final: tuple[ReportSection, ...]


@dataclass(frozen=True, slots=True)
class _ReportInputError(Exception):
    path: Path
    reason: str

    @override
    def __str__(self) -> str:
        return f"{self.reason}: {self.path}"


def _parse_sections(path: Path, text: str) -> tuple[ReportSection, ...]:
    sections: list[ReportSection] = []
    for block in text.strip().split("\n\n"):
        lines = tuple(line.strip() for line in block.splitlines() if line.strip())
        if not lines[1:] or not lines[0].startswith("## "):
            raise _ReportInputError(path=path, reason="report prose is malformed")
        sections.append(ReportSection(lines[0].removeprefix("## "), lines[1:]))
    return tuple(sections)


def load_report_content() -> StaticReportContent:
    """Parse the trusted static report prose asset."""
    path = Path(__file__).with_name("content.md")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise _ReportInputError(
            path=path,
            reason="report prose could not be read",
        ) from error
    pages = text.split("\n---\n")
    if len(pages) != _EXPECTED_PAGE_COUNT:
        raise _ReportInputError(path=path, reason="report prose is malformed")
    return StaticReportContent(
        problem=_parse_sections(path, pages[0]),
        method=_parse_sections(path, pages[1]),
        protocol=_parse_sections(path, pages[2]),
        final=_parse_sections(path, pages[3]),
    )


def _load_model[Metrics: _StrictMetrics](
    path: Path,
    model: type[Metrics],
) -> Metrics:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise _ReportInputError(
            path=path,
            reason="required metric artifact could not be read",
        ) from error
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        raise _ReportInputError(
            path=path,
            reason="metric artifact failed strict validation",
        ) from error


def load_metrics(metrics_dir: Path) -> ReportMetrics:
    """Parse all required evaluator artifacts into one immutable value."""
    return ReportMetrics(
        security=_load_model(metrics_dir / "security.json", _SecurityMetrics),
        quality=_load_model(metrics_dir / "quality.json", _QualityMetrics),
        runtime=_load_model(metrics_dir / "runtime.json", _RuntimeMetrics),
    )
