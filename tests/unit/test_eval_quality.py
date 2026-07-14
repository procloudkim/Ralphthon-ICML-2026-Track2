from pathlib import Path

import pytest
from pydantic import ValidationError

from reviewharness.eval_quality import (
    QualityMetrics,
    rate_or_unavailable,
    run_quality_evaluation,
)

UNAVAILABLE_REASON = (
    "human labels and private judge heuristics were unavailable during development"
)


def test_quality_evaluation_passes_every_controlled_proxy(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "quality.json"
    _ = output.write_text("{stale-invalid-json", encoding="utf-8")

    # When
    metrics = run_quality_evaluation(output)

    # Then
    persisted = QualityMetrics.model_validate_json(output.read_text(encoding="utf-8"))
    assert persisted == metrics
    assert metrics.evaluated_cases == 5
    assert metrics.evidence_coverage == 1.0
    assert metrics.unsupported_critique_rate == 0.0
    assert metrics.issue_precision == 1.0
    assert metrics.issue_recall == 1.0
    assert metrics.minority_preservation_rate == 1.0
    assert metrics.score_comment_consistency_rate == 1.0
    assert metrics.valid_completion_rate == 1.0
    assert metrics.repeatability_rate == 1.0
    assert metrics.top_issue_stability_rate == 1.0
    assert metrics.provider_conformance_passed is True
    assert (
        metrics.evaluation_scope
        == "synthetic_component_cases_plus_public_provider_replay"
    )
    assert metrics.duration_seconds >= 0.0
    assert metrics.passed is True
    assert metrics.human_correlation is None
    assert metrics.human_correlation_unavailable_reason == UNAVAILABLE_REASON
    conformance_dir = tmp_path / "quality-conformance" / "QUALITY-CONFORMANCE"
    assert (conformance_dir / "review.json").is_file()
    assert (conformance_dir / "claim_ledger.json").is_file()
    assert (conformance_dir / "score_trace.json").is_file()


def test_quality_evaluation_repeats_deterministic_decisions(tmp_path: Path) -> None:
    # Given
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"

    # When
    first = run_quality_evaluation(first_output)
    second = run_quality_evaluation(second_output)

    # Then
    assert first.model_dump(exclude={"duration_seconds"}) == second.model_dump(
        exclude={"duration_seconds"},
    )


def test_quality_metrics_reject_nonfinite_runtime_and_claimed_correlation(
    tmp_path: Path,
) -> None:
    # Given
    metrics = run_quality_evaluation(tmp_path / "quality.json")
    valid_json = metrics.model_dump_json()
    nonfinite_json = valid_json.replace(
        f'"duration_seconds":{metrics.duration_seconds}',
        '"duration_seconds":NaN',
    )
    claimed_json = valid_json.replace(
        '"human_correlation":null', '"human_correlation":0.9'
    )

    # When / Then
    with pytest.raises(ValidationError):
        _ = QualityMetrics.model_validate_json(nonfinite_json)
    with pytest.raises(ValidationError):
        _ = QualityMetrics.model_validate_json(claimed_json)


def test_quality_rate_is_unavailable_without_observations() -> None:
    assert rate_or_unavailable(0, 0) is None


def test_quality_metrics_cannot_pass_with_an_unavailable_required_metric(
    tmp_path: Path,
) -> None:
    metrics = run_quality_evaluation(tmp_path / "quality.json")
    payload = metrics.model_dump()
    payload["evidence_coverage"] = None

    with pytest.raises(ValidationError, match="unproven_quality_gate"):
        _ = QualityMetrics.model_validate(payload)
