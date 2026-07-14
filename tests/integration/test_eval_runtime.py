import json
from pathlib import Path

from reviewharness.eval_runtime import (
    RuntimeMetrics,
    RuntimeRunConfig,
    run_runtime_evaluation,
)


def test_runtime_evaluation_proves_ten_paper_production_contract(
    tmp_path: Path,
) -> None:
    # Given: an isolated destination for the measured offline evaluator.
    output = tmp_path / "runtime.json"

    # When: the real kernel and batch runner process the controlled corpus.
    metrics = run_runtime_evaluation(output)

    # Then: the strict artifact proves the complete local production contract.
    assert RuntimeMetrics.model_validate_json(output.read_text("utf-8")) == metrics
    assert set(metrics.model_dump()) == {
        "paper_count",
        "distinct_pdf_count",
        "valid_completion_count",
        "total_seconds",
        "p50_seconds",
        "p95_seconds",
        "timeout_count",
        "retry_count",
        "fast_mode_fallback_count",
        "invalid_output_count",
        "failure_isolation_passed",
        "full_mode_executed",
        "fast_mode_executed",
        "monotonic_deadline",
        "evaluation_scope",
        "provider_scope",
        "real_provider_ten_paper_runtime_status",
        "real_provider_ten_paper_runtime_seconds",
    }
    assert metrics.paper_count == 10
    assert metrics.distinct_pdf_count == 10
    assert metrics.valid_completion_count == 10
    assert metrics.total_seconds < 1_500.0
    assert 0.0 < metrics.p50_seconds <= metrics.p95_seconds
    assert metrics.timeout_count == 0
    assert metrics.retry_count == 0
    assert metrics.fast_mode_fallback_count == 0
    assert metrics.invalid_output_count == 0
    assert metrics.failure_isolation_passed
    assert metrics.full_mode_executed
    assert metrics.fast_mode_executed
    assert metrics.monotonic_deadline
    assert metrics.evaluation_scope == "local_synthetic_hash_distinct_pdf_batch"
    assert metrics.provider_scope == "local_heuristic_no_network"
    assert metrics.real_provider_ten_paper_runtime_status == "unverified"
    assert metrics.real_provider_ten_paper_runtime_seconds is None
    artifact_roots = tuple(tmp_path.glob("runtime-artifacts-*"))
    assert len(artifact_roots) == 1
    primary = artifact_roots[0] / "primary"
    forced = artifact_roots[0] / "failure-isolation"
    run_config = RuntimeRunConfig.model_validate_json(
        (artifact_roots[0] / "run_config.json").read_text("utf-8")
    )
    assert run_config.deadline_seconds == 1_500.0
    assert (run_config.paper_concurrency, run_config.model_call_concurrency) == (5, 10)
    assert run_config.clock == "time.monotonic"
    assert len((primary / "completions.jsonl").read_text("utf-8").splitlines()) == 10
    assert len(list(primary.glob("SAMPLE-*/final_review.json"))) == 10
    hashes = {
        json.loads(path.read_text("utf-8"))["sha256"]
        for path in primary.glob("SAMPLE-*/pdf_hash.json")
    }
    assert len(hashes) == 10
    assert len((forced / "completions.jsonl").read_text("utf-8").splitlines()) == 3
    assert (forced / "SAMPLE-001" / "final_review.json").is_file()
