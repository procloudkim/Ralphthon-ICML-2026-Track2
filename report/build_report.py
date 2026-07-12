"""Build the anonymous ReviewHarness report from saved evaluator artifacts."""

import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import wrap
from typing import Annotated, Final, Protocol, override

import typer
from reportlab.pdfgen.canvas import Canvas

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report import (
    ReportMetrics,
    ReportSection,
    load_metrics,
    load_report_content,
)

PAGE_SIZE: Final = (612.0, 792.0)
LEFT: Final = 44.0
RIGHT: Final = 568.0
TOP: Final = 748.0
BOTTOM: Final = 48.0
BODY_FONT: Final = 8.4
LINE_HEIGHT: Final = 10.8
TITLE: Final = (
    "ReviewHarness: Injection-Resilient Evidence-Weighted Review "
    "under Hidden Human Evaluation"
)


@dataclass(frozen=True, slots=True)
class _ReportPage:
    heading: str
    sections: tuple[ReportSection, ...]


@dataclass(frozen=True, slots=True)
class _ReportLayoutError(Exception):
    page_heading: str

    @override
    def __str__(self) -> str:
        return f"report content exceeded its page: {self.page_heading}"


class _PdfCanvas(Protocol):
    def setAuthor(self, author: str) -> None: ...  # noqa: N802
    def setCreator(self, creator: str) -> None: ...  # noqa: N802
    def setSubject(self, subject: str) -> None: ...  # noqa: N802
    def setTitle(self, title: str) -> None: ...  # noqa: N802
    def setFont(  # noqa: N802
        self,
        psfontname: str,
        size: float,
        leading: float | None = None,
    ) -> None: ...
    def drawString(self, x: float, y: float, text: str) -> None: ...  # noqa: N802
    def drawRightString(self, x: float, y: float, text: str) -> None: ...  # noqa: N802
    def line(self, x1: float, y1: float, x2: float, y2: float) -> None: ...
    def showPage(self) -> None: ...  # noqa: N802
    def save(self) -> None: ...


def _percent(value: float) -> str:
    return f"{value:.1%}"


def _joined(parts: tuple[str, ...]) -> str:
    return "".join(parts)


def _make_pages(metrics: ReportMetrics) -> tuple[_ReportPage, ...]:
    security = metrics.security
    quality = metrics.quality
    runtime = metrics.runtime
    content = load_report_content()
    security_section = ReportSection(
        "Security results",
        (
            _joined(
                (
                    f"Across {security.evaluated_cases} evaluated cases, ",
                    "attack success ",
                    f"was {_percent(security.attack_success_rate)}, ",
                    "marker leakage was ",
                    f"{_percent(security.marker_leakage_rate)}, and unauthorized tool ",
                    f"calls were {security.unauthorized_tool_calls}.",
                )
            ),
            _joined(
                (
                    "Trusted-ID invariance was ",
                    f"{_percent(security.trusted_id_invariance_rate)}, ",
                    "valid completion ",
                    f"was {_percent(security.valid_completion_rate)}, benign false ",
                    f"positives were {_percent(security.benign_false_positive_rate)}, ",
                    "and evaluation duration was ",
                    f"{security.duration_seconds:.3f} seconds.",
                )
            ),
            _joined(
                (
                    f"Detection recall was {_percent(security.detection_recall)}, ",
                    "clean-versus-injected score delta was ",
                    f"{security.clean_injected_score_delta:.3f}, and ",
                    "clean-versus-injected ",
                    "issue overlap was ",
                    f"{_percent(security.clean_injected_issue_overlap)}. Scope: ",
                    f"{security.evaluation_scope}.",
                )
            ),
        ),
    )
    quality_section = ReportSection(
        "Quality results",
        (
            _joined(
                (
                    f"Across {quality.evaluated_cases} controlled cases, evidence ",
                    f"coverage was {_percent(quality.evidence_coverage)} and ",
                    "unsupported ",
                    f"critique was {_percent(quality.unsupported_critique_rate)}.",
                )
            ),
            _joined(
                (
                    f"Issue precision was {_percent(quality.issue_precision)}, issue ",
                    f"recall was {_percent(quality.issue_recall)}, ",
                    "minority preservation ",
                    f"was {_percent(quality.minority_preservation_rate)}, and ",
                    "score-comment consistency was ",
                    f"{_percent(quality.score_comment_consistency_rate)}.",
                )
            ),
            _joined(
                (
                    f"Valid completion was {_percent(quality.valid_completion_rate)}, ",
                    f"repeatability was {_percent(quality.repeatability_rate)}, ",
                    "top-issue ",
                    f"stability was {_percent(quality.top_issue_stability_rate)}, and ",
                    f"evaluation duration was {quality.duration_seconds:.3f} seconds. ",
                    f"The quality gate {'passed' if quality.passed else 'failed'}.",
                )
            ),
            _joined(
                (
                    "Human correlation: N/A (unavailable; ",
                    f"{quality.human_correlation_unavailable_reason}).",
                )
            ),
        ),
    )
    runtime_section = ReportSection(
        "Runtime results",
        (
            _joined(
                (
                    f"The batch produced {runtime.valid_completion_count} valid ",
                    f"completions from {runtime.paper_count} papers in ",
                    f"{runtime.total_seconds:.3f} seconds. Per-paper p50 was ",
                    f"{runtime.p50_seconds:.3f} seconds and p95 was ",
                    f"{runtime.p95_seconds:.3f} seconds.",
                )
            ),
            _joined(
                (
                    f"Timeouts were {runtime.timeout_count}; retries were ",
                    f"{runtime.retry_count}; fast-mode fallbacks were ",
                    f"{runtime.fast_mode_fallback_count}; invalid outputs were ",
                    f"{runtime.invalid_output_count}.",
                )
            ),
            _joined(
                (
                    "Failure isolation ",
                    f"{'passed' if runtime.failure_isolation_passed else 'failed'}. ",
                    "Full mode ",
                    f"{'passed' if runtime.full_mode_executed else 'failed'}; ",
                    "fast mode ",
                    f"{'passed' if runtime.fast_mode_executed else 'failed'}; ",
                    "monotonic deadline control ",
                    f"{'passed' if runtime.monotonic_deadline else 'failed'}.",
                )
            ),
        ),
    )
    return (
        _ReportPage("Problem and contributions", content.problem),
        _ReportPage("Method", content.method),
        _ReportPage(
            "Evaluation and measured proxies",
            (*content.protocol, security_section, quality_section),
        ),
        _ReportPage(
            "Runtime, failure analysis, and limitations",
            (runtime_section, *content.final),
        ),
    )


def _draw_paragraph(pdf: _PdfCanvas, text: str, y: float) -> float:
    pdf.setFont("Helvetica", BODY_FONT)
    lines = wrap(text, width=112, break_long_words=False, break_on_hyphens=False)
    for line_text in lines:
        pdf.drawString(LEFT, y, line_text)
        y -= LINE_HEIGHT
    return y - 3.0


def _draw_page(pdf: _PdfCanvas, page: _ReportPage, page_number: int) -> None:
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(LEFT, 770.0, "ANONYMOUS TECHNICAL REPORT")
    pdf.drawRightString(RIGHT, 770.0, f"Page {page_number}")
    pdf.line(LEFT, 764.0, RIGHT, 764.0)
    pdf.setFont("Helvetica-Bold", 13.0)
    title_lines = tuple(wrap(TITLE, width=72)) if page_number == 1 else (page.heading,)
    y = TOP
    for title_line in title_lines:
        pdf.drawString(LEFT, y, title_line)
        y -= 16.0
    y -= 12.0
    if page_number == 1:
        pdf.setFont("Helvetica-Oblique", 8.5)
        pdf.drawString(LEFT, y, "Anonymous technical report")
        y -= 24.0
    for section in page.sections:
        pdf.setFont("Helvetica-Bold", 10.0)
        pdf.drawString(LEFT, y, section.heading)
        y -= 15.0
        for paragraph in section.paragraphs:
            y = _draw_paragraph(pdf, paragraph, y)
        y -= 6.0
    if y < BOTTOM:
        raise _ReportLayoutError(page_heading=page.heading)
    pdf.showPage()


def build_report(metrics_dir: Path, output: Path) -> Path:
    """Create a four-page anonymous PDF from strict saved metric artifacts."""
    metrics = load_metrics(metrics_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf: _PdfCanvas = Canvas(str(output), pagesize=PAGE_SIZE, pageCompression=1)
    pdf.setAuthor("Anonymous")
    pdf.setCreator("ReviewHarness artifact report builder")
    pdf.setSubject("Evidence-grounded review under hidden human evaluation")
    pdf.setTitle(TITLE)
    for page_number, page in enumerate(_make_pages(metrics), start=1):
        _draw_page(pdf, page, page_number)
    pdf.save()
    return output


app = typer.Typer(add_completion=False, help="Build the anonymous technical report.")


@app.command()
def main(
    metrics_dir: Annotated[
        Path,
        typer.Option("--metrics-dir", help="Directory containing evaluator JSON."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination PDF path."),
    ],
) -> None:
    """Build a report from saved evaluator metrics."""
    typer.echo(build_report(metrics_dir, output))


if __name__ == "__main__":
    app()
