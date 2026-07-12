"""Strict loader for the trusted ICML review rubric."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, Protocol, Self, override

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

type _YamlKey = str | int
type _YamlValue = (
    None | bool | int | float | str | list[_YamlValue] | dict[_YamlKey, _YamlValue]
)


class _SafeYamlLoader(Protocol):
    def __call__(self, stream: str, /) -> _YamlValue: ...


_CANONICAL_RUBRIC_PATH: Final = (
    Path(__file__).resolve().parents[2] / "rubrics" / "icml_review.yaml"
)
_DIMENSION_BOUNDS: Final = (1, 4)
_OVERALL_BOUNDS: Final = (1, 6)
_CONFIDENCE_BOUNDS: Final = (1, 5)


@dataclass(frozen=True, slots=True)
class RubricConfigError(Exception):
    """A trusted rubric file could not be parsed into the required contract."""

    path: Path
    reason: str

    @override
    def __str__(self) -> str:
        return f"{self.reason}: {self.path}"


class _StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class ScoreScale(_StrictModel):
    """One ordinal score scale and the complete anchor text for its values."""

    bounds: tuple[StrictInt, StrictInt] = Field(alias="range")
    anchors: Mapping[StrictInt, StrictStr]

    @property
    def minimum(self) -> int:
        """Return the lowest valid ordinal."""
        return self.bounds[0]

    @property
    def maximum(self) -> int:
        """Return the highest valid ordinal."""
        return self.bounds[1]

    @model_validator(mode="after")
    def _validate_scale(self) -> Self:
        lower, upper = self.bounds
        if lower < 1 or upper < lower:
            code = "invalid_score_bounds"
            message = "score bounds must be positive and ordered"
            raise PydanticCustomError(code, message)
        expected_scores = set(range(lower, upper + 1))
        if set(self.anchors) != expected_scores:
            code = "incomplete_score_anchors"
            message = (
                "anchors must cover every score in the declared range exactly once"
            )
            raise PydanticCustomError(code, message)
        if any(not anchor.strip() for anchor in self.anchors.values()):
            code = "blank_score_anchor"
            message = "score anchors must contain nonblank text"
            raise PydanticCustomError(code, message)
        return self


class ScoreDefinitions(_StrictModel):
    """The six required ICML review score dimensions."""

    soundness: ScoreScale
    presentation: ScoreScale
    significance: ScoreScale
    originality: ScoreScale
    overall_recommendation: ScoreScale
    confidence: ScoreScale

    @model_validator(mode="after")
    def _validate_official_ranges(self) -> Self:
        dimension_bounds = (
            self.soundness.bounds,
            self.presentation.bounds,
            self.significance.bounds,
            self.originality.bounds,
        )
        if any(bounds != _DIMENSION_BOUNDS for bounds in dimension_bounds):
            code = "invalid_dimension_range"
            message = "dimension score ranges must be 1 through 4"
            raise PydanticCustomError(code, message)
        if self.overall_recommendation.bounds != _OVERALL_BOUNDS:
            code = "invalid_overall_range"
            message = "overall recommendation range must be 1 through 6"
            raise PydanticCustomError(code, message)
        if self.confidence.bounds != _CONFIDENCE_BOUNDS:
            code = "invalid_confidence_range"
            message = "confidence range must be 1 through 5"
            raise PydanticCustomError(code, message)
        return self


class RubricPolicies(_StrictModel):
    """Required trusted policy switches controlling review and calibration."""

    extreme_scores_require_extreme_evidence: StrictBool
    weak_accept_and_weak_reject_use_sparingly: StrictBool
    confidence_is_not_paper_quality: StrictBool
    do_not_average_reviewer_scores: StrictBool
    require_evidence_for_major_factual_findings: StrictBool
    preserve_verified_minority_findings: StrictBool
    injection_detection_is_not_a_scientific_penalty: StrictBool


class RubricConfig(_StrictModel):
    """Validated machine-readable ICML rubric used by the trusted control plane."""

    version: StrictStr
    scope: StrictStr
    scores: ScoreDefinitions
    policies: RubricPolicies
    consistency_guards: tuple[StrictStr, ...] = Field(min_length=1)

    @field_validator("version", "scope")
    @classmethod
    def _require_nonblank_metadata(cls, value: str) -> str:
        if not value.strip():
            code = "blank_rubric_metadata"
            message = "rubric metadata must contain nonblank text"
            raise PydanticCustomError(code, message)
        return value

    @field_validator("consistency_guards")
    @classmethod
    def _validate_consistency_guards(cls, guards: tuple[str, ...]) -> tuple[str, ...]:
        if any(not guard.strip() for guard in guards):
            code = "blank_consistency_guard"
            message = "consistency guards must contain nonblank text"
            raise PydanticCustomError(code, message)
        if len(set(guards)) != len(guards):
            code = "duplicate_consistency_guard"
            message = "consistency guards must be unique"
            raise PydanticCustomError(code, message)
        return guards


def _load_yaml(source: str, loader: _SafeYamlLoader = yaml.safe_load) -> _YamlValue:
    return loader(source)


def load_rubric(path: Path | None = None) -> RubricConfig:
    """Load and strictly parse a rubric, defaulting to the repository canonical file."""
    rubric_path = _CANONICAL_RUBRIC_PATH if path is None else path
    try:
        source = rubric_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RubricConfigError(
            path=rubric_path,
            reason="rubric file could not be read",
        ) from error

    try:
        untrusted_yaml = _load_yaml(source)
    except yaml.YAMLError as error:
        raise RubricConfigError(
            path=rubric_path,
            reason="rubric YAML is malformed",
        ) from error

    try:
        return RubricConfig.model_validate(untrusted_yaml)
    except ValidationError as error:
        raise RubricConfigError(
            path=rubric_path,
            reason="rubric content failed validation",
        ) from error
