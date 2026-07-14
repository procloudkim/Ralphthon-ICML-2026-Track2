from math import isfinite
from pathlib import Path

import pytest
from pydantic import ValidationError

from reviewharness.eval_security import SecurityMetrics, run_security_evaluation


def test_security_evaluator_contains_every_control_case(tmp_path: Path) -> None:
    # Given: the repository's twelve-case synthetic security corpus.
    output = tmp_path / "security.json"

    # When: the deterministic local evaluator runs without a live model.
    metrics = run_security_evaluation(output)

    # Then: every blocking security control passes in the declared scope.
    assert metrics.evaluated_cases == 12
    assert metrics.detection_recall == 1.0
    assert metrics.benign_false_positive_rate == 0.0
    assert metrics.attack_success_rate == 0.0
    assert metrics.marker_leakage_rate == 0.0
    assert metrics.unauthorized_tool_calls is None
    assert metrics.unauthorized_tool_calls_status == "unmeasured_no_instrumented_runner"
    assert metrics.trusted_id_invariance_rate == 1.0
    assert metrics.valid_completion_rate == 1.0
    assert metrics.clean_injected_score_delta == 0.0
    assert metrics.clean_injected_issue_overlap == 1.0
    assert metrics.paired_document_count == 1
    assert (
        metrics.evaluation_scope
        == "synthetic_attack_cases_plus_public_paired_documents_local_provider"
    )
    assert metrics.provider_scope == "local_heuristic_no_tools_no_network"
    assert metrics.passed is True


def test_security_evaluator_writes_strict_atomic_json(tmp_path: Path) -> None:
    # Given: a fresh evaluator output path.
    output = tmp_path / "nested" / "security.json"

    # When: the evaluator persists its measured result.
    metrics = run_security_evaluation(output)

    # Then: the artifact round-trips through the strict public schema.
    saved = SecurityMetrics.model_validate_json(output.read_text(encoding="utf-8"))
    assert saved == metrics
    assert isfinite(saved.duration_seconds)
    assert not tuple(output.parent.glob("*.tmp"))


def test_security_metrics_reject_non_finite_duration() -> None:
    # Given: an otherwise valid metrics payload with a non-finite duration.
    # When / Then: boundary parsing rejects the unreportable value.
    with pytest.raises(ValidationError):
        _ = SecurityMetrics(
            evaluated_cases=1,
            detection_recall=1.0,
            benign_false_positive_rate=0.0,
            attack_success_rate=0.0,
            marker_leakage_rate=0.0,
            unauthorized_tool_calls=None,
            unauthorized_tool_calls_status="unmeasured_no_instrumented_runner",
            trusted_id_invariance_rate=1.0,
            valid_completion_rate=1.0,
            clean_injected_score_delta=0.0,
            clean_injected_issue_overlap=1.0,
            paired_document_count=1,
            duration_seconds=float("nan"),
            evaluation_scope=(
                "synthetic_attack_cases_plus_public_paired_documents_local_provider"
            ),
            provider_scope="local_heuristic_no_tools_no_network",
            passed=True,
        )
