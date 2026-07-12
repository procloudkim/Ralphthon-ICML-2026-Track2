"""Strict evaluator-artifact boundary for anonymous report generation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal, override

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError

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
    unauthorized_tool_calls: _Count
    trusted_id_invariance_rate: _Rate
    valid_completion_rate: _Rate
    benign_false_positive_rate: _Rate
    duration_seconds: _NonNegativeMetric
    detection_recall: _Rate
    clean_injected_score_delta: _NonNegativeMetric
    clean_injected_issue_overlap: _Rate
    evaluation_scope: Literal["deterministic_synthetic_fixture_and_provider"]


class _QualityMetrics(_StrictMetrics):
    evaluated_cases: _PositiveCount
    evidence_coverage: _Rate
    unsupported_critique_rate: _Rate
    issue_precision: _Rate
    issue_recall: _Rate
    minority_preservation_rate: _Rate
    score_comment_consistency_rate: _Rate
    valid_completion_rate: _Rate
    repeatability_rate: _Rate
    top_issue_stability_rate: _Rate
    duration_seconds: _NonNegativeMetric
    passed: bool
    human_correlation: None
    human_correlation_unavailable_reason: Literal[
        "human labels and private judge heuristics were unavailable during development"
    ]


class _RuntimeMetrics(_StrictMetrics):
    paper_count: _PositiveCount
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
