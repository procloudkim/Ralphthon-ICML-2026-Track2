from pathlib import Path

import pytest

from reviewharness.config import RubricConfigError, load_rubric

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_RUBRIC = REPOSITORY_ROOT / "rubrics" / "icml_review.yaml"


def test_load_rubric_returns_canonical_strict_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    monkeypatch.chdir(tmp_path)

    # When
    rubric = load_rubric()

    # Then
    assert rubric.version == "icml-2026-ralphthon-v1"
    assert rubric.scores.soundness.bounds == (1, 4)
    assert rubric.scores.soundness.minimum == 1
    assert rubric.scores.soundness.maximum == 4
    assert rubric.scores.overall_recommendation.bounds == (1, 6)
    assert rubric.scores.confidence.bounds == (1, 5)
    assert rubric.scores.soundness.anchors[4].startswith("Excellent:")
    assert rubric.policies.do_not_average_reviewer_scores is True
    assert rubric.consistency_guards[0] == (
        "soundness == 1 implies overall_recommendation <= 2"
    )


def test_load_rubric_raises_typed_error_when_file_is_missing(tmp_path: Path) -> None:
    # Given
    missing_path = tmp_path / "missing.yaml"

    # When
    with pytest.raises(RubricConfigError) as captured:
        _ = load_rubric(missing_path)

    # Then
    assert captured.value.path == missing_path
    assert captured.value.reason == "rubric file could not be read"


def test_load_rubric_fails_closed_on_malformed_yaml(tmp_path: Path) -> None:
    # Given
    rubric_path = tmp_path / "malformed.yaml"
    _ = rubric_path.write_text("scores: [unterminated", encoding="utf-8")

    # When
    with pytest.raises(RubricConfigError) as captured:
        _ = load_rubric(rubric_path)

    # Then
    assert captured.value.reason == "rubric YAML is malformed"


def test_load_rubric_rejects_non_boolean_policy(tmp_path: Path) -> None:
    # Given
    rubric_path = tmp_path / "string-policy.yaml"
    source = CANONICAL_RUBRIC.read_text(encoding="utf-8")
    _ = rubric_path.write_text(
        source.replace(
            "extreme_scores_require_extreme_evidence: true",
            'extreme_scores_require_extreme_evidence: "true"',
            1,
        ),
        encoding="utf-8",
    )

    # When
    with pytest.raises(RubricConfigError) as captured:
        _ = load_rubric(rubric_path)

    # Then
    assert captured.value.reason == "rubric content failed validation"


def test_load_rubric_rejects_non_official_score_range(tmp_path: Path) -> None:
    # Given
    rubric_path = tmp_path / "wrong-range.yaml"
    source = CANONICAL_RUBRIC.read_text(encoding="utf-8")
    _ = rubric_path.write_text(
        source.replace("range: [1, 4]", "range: [0, 4]", 1),
        encoding="utf-8",
    )

    # When
    with pytest.raises(RubricConfigError) as captured:
        _ = load_rubric(rubric_path)

    # Then
    assert captured.value.reason == "rubric content failed validation"


def test_load_rubric_requires_anchor_for_every_score(tmp_path: Path) -> None:
    # Given
    rubric_path = tmp_path / "missing-anchor.yaml"
    source = CANONICAL_RUBRIC.read_text(encoding="utf-8")
    soundness_four_anchor = (
        '      4: "Excellent: central claims are strongly supported and no '
        'substantive technical weakness remains."\n'
    )
    _ = rubric_path.write_text(
        source.replace(soundness_four_anchor, "", 1),
        encoding="utf-8",
    )

    # When
    with pytest.raises(RubricConfigError) as captured:
        _ = load_rubric(rubric_path)

    # Then
    assert captured.value.reason == "rubric content failed validation"


def test_load_rubric_rejects_duplicate_consistency_guard(tmp_path: Path) -> None:
    # Given
    rubric_path = tmp_path / "duplicate-guard.yaml"
    source = CANONICAL_RUBRIC.read_text(encoding="utf-8")
    first_guard = '  - "soundness == 1 implies overall_recommendation <= 2"\n'
    _ = rubric_path.write_text(
        source.replace(first_guard, first_guard * 2, 1),
        encoding="utf-8",
    )

    # When
    with pytest.raises(RubricConfigError) as captured:
        _ = load_rubric(rubric_path)

    # Then
    assert captured.value.reason == "rubric content failed validation"
