"""Expose the documented ReviewHarness command-line contract."""

import json
import re
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal, Never

import anyio
import typer
from pydantic import BaseModel, ConfigDict, ValidationError

from . import __version__
from .deadline import ReviewMode
from .eval_quality import run_quality_evaluation
from .eval_runtime import run_runtime_evaluation
from .eval_security import run_security_evaluation
from .kernel import KernelReviewError, ReviewKernel
from .live_cli import live_command
from .runner import BatchConfig, BatchSummary, run_batch
from .schemas import NonEmptyStr, ReviewSubmission, TrustedAssignment

APP_NAME: Final = "ReviewHarness"
_MARKER: Final = re.compile(r"\bRH_[A-Z0-9_]+\b", re.IGNORECASE)
_URL: Final = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_CAPABILITY: Final = re.compile(
    r"""\b(?:reveal|print|show|send|expose|leak|read|access|run|execute|invoke|
    call|fetch|browse|connect|submit|upload)\b[^\n]{0,80}\b(?:secret|
    credential|password|api[ _-]*key|token|environment|shell|terminal|
    command|powershell|bash|tool|network|url|endpoint)s?\b""",
    re.IGNORECASE,
)
_DEFAULT_REVIEW_OUTPUT: Final = Path("review.json")
_DEFAULT_BATCH_OUTPUT: Final = Path("runs/current")
_DEFAULT_QUALITY_OUTPUT: Final = Path("evals/results/quality.json")
_DEFAULT_SECURITY_OUTPUT: Final = Path("evals/results/security.json")
_DEFAULT_RUNTIME_OUTPUT: Final = Path("evals/results/runtime.json")
_PAPER_COUNT: Final = 10


class _StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )


class _ManifestAssignment(_StrictModel):
    paper_id: NonEmptyStr
    pdf_path: Path
    requested_mode: ReviewMode


class _AssignmentManifest(_StrictModel):
    schema_version: Literal["1.0"]
    provenance: Literal["synthetic_controlled_fixture"]
    assignments: tuple[_ManifestAssignment, ...]


def _root() -> None:
    pass


app = typer.Typer(
    name=APP_NAME,
    help=f"{APP_NAME} {__version__}",
    callback=_root,
    no_args_is_help=True,
)
_ = app.command("live")(live_command)


def _fail(code: str) -> Never:
    typer.echo(code, err=True)
    raise typer.Exit(code=1)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    _ = temporary.write_text(text, encoding="utf-8", newline="\n")
    _ = temporary.replace(path)


async def _review_one(
    assignment: TrustedAssignment,
    mode: ReviewMode,
    output_dir: Path,
) -> ReviewSubmission:
    return await ReviewKernel().review(assignment, mode, output_dir)


@app.command("review")
def review_command(
    paper: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    paper_id: Annotated[str, typer.Option("--paper-id")],
    mode: Annotated[ReviewMode, typer.Option("--mode")] = ReviewMode.FULL,
    output: Annotated[Path, typer.Option("--output")] = _DEFAULT_REVIEW_OUTPUT,
) -> None:
    """Review one local PDF using a trusted paper identifier."""
    try:
        assignment = TrustedAssignment(paper_id=paper_id, pdf_path=paper.resolve())
        submission = anyio.run(_review_one, assignment, mode, output.parent)
        _atomic_text(output, f"{submission.model_dump_json(indent=2)}\n")
    except (KernelReviewError, OSError, ValidationError, ValueError):
        _fail("review_failed")
    typer.echo(f"review_complete paper_id={submission.paper_id} mode={mode.value}")


@app.command("validate")
def validate_command(
    review: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate one strict public review payload."""
    try:
        submission = ReviewSubmission.model_validate_json(
            review.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError):
        _fail("invalid_review")
    comment = submission.comment
    if any(
        pattern.search(comment) is not None for pattern in (_MARKER, _URL, _CAPABILITY)
    ):
        _fail("invalid_review")
    typer.echo(f"valid paper_id={submission.paper_id}")


def _manifest_assignments(path: Path) -> tuple[TrustedAssignment, ...]:
    manifest = _AssignmentManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if len(manifest.assignments) != _PAPER_COUNT:
        raise ValueError
    assignments: list[TrustedAssignment] = []
    for item in manifest.assignments:
        pdf_path = item.pdf_path
        if not pdf_path.is_absolute():
            repository_candidate = (Path.cwd() / pdf_path).resolve()
            pdf_path = (
                repository_candidate
                if repository_candidate.is_file()
                else (path.parent / pdf_path).resolve()
            )
        if not pdf_path.is_file():
            raise ValueError
        assignments.append(TrustedAssignment(paper_id=item.paper_id, pdf_path=pdf_path))
    return tuple(assignments)


def _summary_json(summary: BatchSummary, requested: int) -> str:
    payload = {
        "requested_count": requested,
        "valid_count": summary.completed_count,
        "failed_count": summary.failed_count,
        "fallback_count": summary.fallback_count,
        "total_seconds": summary.total_seconds,
        "within_deadline": summary.within_deadline,
    }
    return f"{json.dumps(payload, indent=2, sort_keys=True)}\n"


@app.command("batch")
def batch_command(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output_dir: Annotated[Path, typer.Option("--output-dir")] = _DEFAULT_BATCH_OUTPUT,
    deadline_seconds: Annotated[
        float, typer.Option("--deadline-seconds", min=1.0)
    ] = 1_500.0,
    paper_concurrency: Annotated[int, typer.Option("--paper-concurrency", min=1)] = 5,
    llm_concurrency: Annotated[int, typer.Option("--llm-concurrency", min=1)] = 10,
) -> None:
    """Review a strict ten-paper assignment manifest with bounded concurrency."""
    try:
        assignments = _manifest_assignments(manifest)
        config = BatchConfig(
            deadline_seconds=deadline_seconds,
            paper_concurrency=paper_concurrency,
            model_call_concurrency=llm_concurrency,
        )
        summary = anyio.run(run_batch, assignments, ReviewKernel(), config, output_dir)
        _atomic_text(
            output_dir / "summary.json", _summary_json(summary, len(assignments))
        )
    except (OSError, UnicodeError, ValidationError, ValueError):
        _fail("invalid_assignment_manifest")
    for item in summary.items:
        typer.echo(
            f"paper_complete paper_id={item.paper_id} status={item.status.value}"
        )
    typer.echo(
        f"batch_complete valid={summary.completed_count} requested={len(assignments)}"
    )
    if summary.completed_count != len(assignments) or not summary.within_deadline:
        raise typer.Exit(code=1)


@app.command("eval-quality")
def eval_quality_command(
    output: Annotated[Path, typer.Option("--output")] = _DEFAULT_QUALITY_OUTPUT,
) -> None:
    """Run deterministic quality proxy evaluation."""
    metrics = run_quality_evaluation(output)
    typer.echo(f"evaluation_complete output={output}")
    if not metrics.passed:
        raise typer.Exit(code=1)


@app.command("eval-security")
def eval_security_command(
    output: Annotated[Path, typer.Option("--output")] = _DEFAULT_SECURITY_OUTPUT,
) -> None:
    """Run deterministic prompt-injection security evaluation."""
    metrics = run_security_evaluation(output)
    typer.echo(f"evaluation_complete output={output}")
    if not metrics.passed:
        raise typer.Exit(code=1)


@app.command("eval-runtime")
def eval_runtime_command(
    output: Annotated[Path, typer.Option("--output")] = _DEFAULT_RUNTIME_OUTPUT,
) -> None:
    """Run measured offline production-runtime evaluation."""
    _ = run_runtime_evaluation(output)
    typer.echo(f"evaluation_complete output={output}")


def main() -> None:
    """Run the ReviewHarness command-line application."""
    app(prog_name=APP_NAME)
