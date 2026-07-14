import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
METRICS_FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "report"
REPORT_SCRIPT = REPOSITORY_ROOT / "report" / "build_report.py"
PDF_PROBE = """
import sys
import pymupdf

with pymupdf.open(sys.argv[1]) as document:
    print(document.page_count)
    print("\\n".join(page.get_text() for page in document))
"""


def _run_report(metrics_dir: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and local script
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--metrics-dir",
            str(metrics_dir),
            "--output",
            str(output),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _inspect_pdf(path: Path) -> tuple[int, str]:
    result = subprocess.run(  # noqa: S603 - fixed interpreter and local probe
        [sys.executable, "-c", PDF_PROBE, str(path)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    first_line, text = result.stdout.split("\n", maxsplit=1)
    return int(first_line), text


def _copy_metrics(tmp_path: Path) -> Path:
    metrics_dir = tmp_path / "metrics"
    _ = shutil.copytree(METRICS_FIXTURE, metrics_dir)
    return metrics_dir


def test_build_report_writes_anonymous_four_page_pdf(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "reviewharness-report.pdf"

    # When
    result = _run_report(METRICS_FIXTURE, output)

    # Then
    assert result.returncode == 0, result.stderr
    page_count, text = _inspect_pdf(output)
    assert page_count <= 4
    assert "Anonymous technical report" in text
    assert "Human correlation: N/A" in text
    assert "unmeasured (no instrumented runner)" in text
    assert "Real-provider ten-paper runtime remains unverified" in text
    assert "92.0%" in text
    assert "C:\\" not in text


def test_build_report_sources_changed_numeric_claim_from_artifact(
    tmp_path: Path,
) -> None:
    # Given
    metrics_dir = _copy_metrics(tmp_path)
    security_path = metrics_dir / "security.json"
    _ = security_path.write_text(
        security_path.read_text(encoding="utf-8").replace(
            '"attack_success_rate": 0.0',
            '"attack_success_rate": 0.125',
        ),
        encoding="utf-8",
    )

    # When
    output = tmp_path / "derived.pdf"
    result = _run_report(metrics_dir, output)

    # Then
    assert result.returncode == 0, result.stderr
    _, text = _inspect_pdf(output)
    assert "12.5%" in text


def test_build_report_fails_closed_when_metric_file_is_missing(
    tmp_path: Path,
) -> None:
    # Given
    metrics_dir = _copy_metrics(tmp_path)
    (metrics_dir / "security.json").unlink()

    # When
    result = _run_report(metrics_dir, tmp_path / "missing.pdf")

    # Then
    assert result.returncode != 0
    assert "required metric artifact could not be read" in result.stderr


def test_build_report_requires_security_gate_result(tmp_path: Path) -> None:
    # Given
    metrics_dir = _copy_metrics(tmp_path)
    security_path = metrics_dir / "security.json"
    _ = security_path.write_text(
        security_path.read_text(encoding="utf-8").replace(
            ',\n  "passed": true',
            "",
        ),
        encoding="utf-8",
    )

    # When
    result = _run_report(metrics_dir, tmp_path / "missing-security-gate.pdf")

    # Then
    assert result.returncode != 0
    assert "metric artifact failed strict validation" in result.stderr


def test_build_report_fails_closed_on_malformed_json(tmp_path: Path) -> None:
    # Given
    metrics_dir = _copy_metrics(tmp_path)
    malformed_path = metrics_dir / "quality.json"
    _ = malformed_path.write_text("{not-json", encoding="utf-8")

    # When
    result = _run_report(metrics_dir, tmp_path / "malformed.pdf")

    # Then
    assert result.returncode != 0
    assert "metric artifact failed strict validation" in result.stderr


def test_build_report_fails_closed_on_nonfinite_metric(tmp_path: Path) -> None:
    # Given
    metrics_dir = _copy_metrics(tmp_path)
    runtime_path = metrics_dir / "runtime.json"
    _ = runtime_path.write_text(
        runtime_path.read_text(encoding="utf-8").replace(
            '"total_seconds": 2.5',
            '"total_seconds": NaN',
        ),
        encoding="utf-8",
    )

    # When
    result = _run_report(metrics_dir, tmp_path / "nonfinite.pdf")

    # Then
    assert result.returncode != 0
    assert "metric artifact failed strict validation" in result.stderr


def test_build_report_rejects_human_correlation_claim(tmp_path: Path) -> None:
    # Given
    metrics_dir = _copy_metrics(tmp_path)
    quality_path = metrics_dir / "quality.json"
    _ = quality_path.write_text(
        quality_path.read_text(encoding="utf-8").replace(
            '"human_correlation": null',
            '"human_correlation": 0.99',
        ),
        encoding="utf-8",
    )

    # When
    result = _run_report(metrics_dir, tmp_path / "fabricated.pdf")

    # Then
    assert result.returncode != 0
    assert "metric artifact failed strict validation" in result.stderr


def test_build_report_renders_unavailable_quality_metric_as_na(
    tmp_path: Path,
) -> None:
    metrics_dir = _copy_metrics(tmp_path)
    quality_path = metrics_dir / "quality.json"
    text = quality_path.read_text(encoding="utf-8")
    text = text.replace('"evidence_coverage": 0.92', '"evidence_coverage": null')
    text = text.replace('"passed": true', '"passed": false')
    _ = quality_path.write_text(text, encoding="utf-8")

    output = tmp_path / "unavailable.pdf"
    result = _run_report(metrics_dir, output)

    assert result.returncode == 0, result.stderr
    _, rendered = _inspect_pdf(output)
    assert "evidence coverage was N/A" in rendered


def test_build_report_rejects_passing_quality_gate_with_unavailable_metric(
    tmp_path: Path,
) -> None:
    metrics_dir = _copy_metrics(tmp_path)
    quality_path = metrics_dir / "quality.json"
    _ = quality_path.write_text(
        quality_path.read_text(encoding="utf-8").replace(
            '"evidence_coverage": 0.92',
            '"evidence_coverage": null',
        ),
        encoding="utf-8",
    )

    result = _run_report(metrics_dir, tmp_path / "unproven-quality.pdf")

    assert result.returncode != 0
    assert "metric artifact failed strict validation" in result.stderr
