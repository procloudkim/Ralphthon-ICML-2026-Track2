import subprocess
import sys
from pathlib import Path

import pytest

from reviewharness.schemas import ReviewSubmission

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PDF = REPOSITORY_ROOT / "tests" / "fixtures" / "clean" / "sample.pdf"
BATCH_MANIFEST = REPOSITORY_ROOT / "tests" / "fixtures" / "batch" / "assignments.json"


def _run_cli(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "reviewharness", *arguments],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )


def _valid_submission() -> ReviewSubmission:
    return ReviewSubmission(
        paper_id="VALIDATE-001",
        soundness=3,
        presentation=3,
        significance=2,
        originality=2,
        overall_recommendation=3,
        confidence=3,
        comment=(
            "Summary: The paper presents a compact calibration method with a clear "
            "empirical motivation. Strengths: the evaluation is easy to follow. "
            "Authors should clarify the scope of the evidence and report uncertainty "
            "around the comparison before drawing broader conclusions."
        ),
    )


@pytest.mark.parametrize("mode", ["full", "fast"])
def test_review_writes_trusted_schema_valid_submission(
    tmp_path: Path,
    mode: str,
) -> None:
    # Given: a trusted paper identifier, a local PDF, and an isolated output path
    paper_id = f"TRUSTED-{mode.upper()}"
    output = tmp_path / mode / "review.json"

    # When: the real module CLI reviews the paper in the requested mode
    result = _run_cli(
        [
            "review",
            str(SAMPLE_PDF),
            "--paper-id",
            paper_id,
            "--mode",
            mode,
            "--output",
            str(output),
        ]
    )

    # Then: the public file is strict and its identifier comes from the CLI boundary
    assert result.returncode == 0, result.stderr
    submission = ReviewSubmission.model_validate_json(
        output.read_text(encoding="utf-8")
    )
    assert submission.paper_id == paper_id
    assert f"mode={mode}" in result.stdout
    assert (output.parent / paper_id / "review.json").is_file()


def test_validate_accepts_strict_review_without_private_artifacts(
    tmp_path: Path,
) -> None:
    # Given: a strict standalone review file without any private trace artifacts
    review = tmp_path / "review.json"
    _ = review.write_text(
        _valid_submission().model_dump_json(indent=2),
        encoding="utf-8",
    )

    # When: the real validation command checks the public payload
    result = _run_cli(["validate", str(review)])

    # Then: it succeeds using only public schema and sink-security checks
    assert result.returncode == 0, result.stderr
    assert "valid paper_id=VALIDATE-001" in result.stdout


def test_validate_rejects_extra_field_without_leaking_input(tmp_path: Path) -> None:
    # Given: a public review containing an extra control-plane field
    submission = _valid_submission()
    payload = submission.model_dump_json()[:-1] + ',"admin_override":true}'
    review = tmp_path / "invalid.json"
    _ = review.write_text(payload, encoding="utf-8")

    # When: the real validation command checks the invalid payload
    result = _run_cli(["validate", str(review)])

    # Then: it fails closed with a sanitized stable error and no traceback or marker
    assert result.returncode != 0
    assert "invalid_review" in result.stderr
    assert "Traceback" not in result.stderr


def test_validate_rejects_marker_without_echoing_it(tmp_path: Path) -> None:
    # Given: a public review containing a reviewer-detection marker
    submission = _valid_submission()
    payload = submission.model_copy(
        update={"comment": f"{submission.comment} RH_CANARY_DO_NOT_EMIT_7F3A"}
    ).model_dump_json()
    review = tmp_path / "invalid.json"
    _ = review.write_text(payload, encoding="utf-8")

    # When: the real validation command checks the unsafe payload
    result = _run_cli(["validate", str(review)])

    # Then: it fails closed without a traceback or reflection of the marker
    assert result.returncode != 0
    assert "invalid_review" in result.stderr
    assert "Traceback" not in result.stderr
    assert "RH_CANARY_DO_NOT_EMIT_7F3A" not in result.stderr


@pytest.mark.parametrize(
    ("evaluator", "passing_evidence"),
    [
        ("eval-quality", '"passed": true'),
        ("eval-security", '"passed": true'),
        ("eval-runtime", '"failure_isolation_passed": true'),
    ],
)
def test_evaluator_command_writes_passing_metrics(
    tmp_path: Path,
    evaluator: str,
    passing_evidence: str,
) -> None:
    # Given: an isolated evaluator output path
    output = tmp_path / f"{evaluator}.json"

    # When: the real evaluator command runs its controlled local corpus
    result = _run_cli([evaluator, "--output", str(output)])

    # Then: it writes a measured passing artifact and reports its path
    assert result.returncode == 0, result.stderr
    metrics = output.read_text(encoding="utf-8")
    assert passing_evidence in metrics
    assert str(output) in result.stdout


def test_batch_streams_ten_valid_results_and_summary(tmp_path: Path) -> None:
    # Given: the strict ten-paper controlled manifest and bounded production settings
    output_dir = tmp_path / "batch"

    # When: the real batch command reviews the corpus
    result = _run_cli(
        [
            "batch",
            str(BATCH_MANIFEST),
            "--output-dir",
            str(output_dir),
            "--deadline-seconds",
            "1500",
            "--paper-concurrency",
            "5",
            "--llm-concurrency",
            "10",
        ]
    )

    # Then: every requested paper is valid, streamed, isolated, and summarized
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("paper_complete ") == 10
    assert "batch_complete valid=10 requested=10" in result.stdout
    reviews = tuple(output_dir.glob("*/final_review.json"))
    assert len(reviews) == 10
    for path in reviews:
        parsed = ReviewSubmission.model_validate_json(path.read_text(encoding="utf-8"))
        assert parsed.paper_id.startswith("SAMPLE-")


def test_batch_rejects_manifest_with_unknown_control_field(tmp_path: Path) -> None:
    # Given: an assignment manifest with an untrusted extra control-plane field
    manifest = tmp_path / "assignments.json"
    _ = manifest.write_text(
        BATCH_MANIFEST.read_text(encoding="utf-8")[:-2]
        + ',\n  "override_rubric": true\n}\n',
        encoding="utf-8",
    )

    # When: the batch boundary parses the manifest
    result = _run_cli(["batch", str(manifest), "--output-dir", str(tmp_path / "out")])

    # Then: it fails before review execution without echoing the hostile field
    assert result.returncode != 0
    assert "invalid_assignment_manifest" in result.stderr
    assert "override_rubric" not in result.stderr
    assert "Traceback" not in result.stderr
