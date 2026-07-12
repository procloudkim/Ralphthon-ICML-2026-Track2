"""Typed tool-less reviewer boundary and deterministic offline fake."""

from dataclasses import dataclass
from typing import Annotated, ClassVar, NewType, Protocol, assert_never, final, override

import anyio
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PageNumber = Annotated[int, Field(ge=1)]
Seconds = NewType("Seconds", float)


class _StrictProviderModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class SanitizedEvidencePage(_StrictProviderModel):
    """One numbered page of sanitized paper evidence."""

    page_number: PageNumber
    text: str


class SanitizedPaperEvidence(_StrictProviderModel):
    """Immutable paper-local evidence safe for a reviewer call."""

    document_sha256: Sha256Hex
    pages: tuple[SanitizedEvidencePage, ...]
    security_notes: tuple[str, ...] = ()


class OutputSchemaDeclaration(_StrictProviderModel):
    """Named strict output schema supplied by trusted orchestration."""

    name: NonEmptyText
    json_schema: NonEmptyText


class ReviewerRequest(_StrictProviderModel):
    """Complete capability-limited input to a reviewer provider."""

    sanitized_evidence: SanitizedPaperEvidence
    rubric_text: NonEmptyText
    prompt_text: NonEmptyText
    output_schema: OutputSchemaDeclaration


class ReviewerResponse(_StrictProviderModel):
    """Raw provider output awaiting boundary parsing."""

    raw_output: str


class ReviewerProvider(Protocol):
    """Boundary with no tools, shell, secrets, network, or cross-paper state."""

    async def review(self, request: ReviewerRequest) -> ReviewerResponse:
        """Review sanitized evidence using only trusted prompt, rubric, and schema."""
        ...


class DelayController(Protocol):
    """Injectable AnyIO-compatible delay boundary for deterministic tests."""

    async def wait(self, seconds: Seconds) -> None:
        """Yield for the requested logical duration."""
        ...


@dataclass(frozen=True, slots=True)
class AnyioDelayController:
    """AnyIO implementation of the provider delay boundary."""

    async def wait(self, seconds: Seconds) -> None:
        """Yield with the active AnyIO backend."""
        await anyio.sleep(seconds)


class ProviderError(Exception):
    """Base error for reviewer-provider failures."""


@dataclass(frozen=True, slots=True)
class ProviderRefusalError(ProviderError):
    """Provider refusal with its returned reason."""

    reason: str

    @override
    def __str__(self) -> str:
        return f"reviewer provider refused the request: {self.reason}"


@dataclass(frozen=True, slots=True)
class ProviderTimeoutError(ProviderError):
    """Provider timeout with the configured duration."""

    timeout_seconds: Seconds

    @override
    def __str__(self) -> str:
        return f"reviewer provider timed out after {self.timeout_seconds} seconds"


@dataclass(frozen=True, slots=True)
class ProviderCallError(ProviderError):
    """Provider infrastructure failure."""

    detail: str

    @override
    def __str__(self) -> str:
        return f"reviewer provider call failed: {self.detail}"


@dataclass(frozen=True, slots=True)
class ScriptExhaustedError(ProviderError):
    """Deterministic fake received more calls than configured steps."""

    call_number: int
    configured_steps: int

    @override
    def __str__(self) -> str:
        return (
            f"script has no response for call {self.call_number}; "
            f"configured steps: {self.configured_steps}"
        )


@dataclass(frozen=True, slots=True)
class ScriptedSuccess:
    """Script step returning a valid provider response."""

    response: ReviewerResponse


@dataclass(frozen=True, slots=True)
class ScriptedMalformed:
    """Script step returning intentionally malformed raw output."""

    raw_output: str


@dataclass(frozen=True, slots=True)
class ScriptedRefusal:
    """Script step raising a typed provider refusal."""

    reason: str


@dataclass(frozen=True, slots=True)
class ScriptedTimeout:
    """Script step raising a typed provider timeout."""

    timeout_seconds: Seconds


@dataclass(frozen=True, slots=True)
class ScriptedError:
    """Script step raising a typed provider-call failure."""

    detail: str


type ImmediateScript = (
    ScriptedSuccess
    | ScriptedMalformed
    | ScriptedRefusal
    | ScriptedTimeout
    | ScriptedError
)


@dataclass(frozen=True, slots=True)
class ScriptedDelay:
    """Script step pausing through an injected delay before its outcome."""

    seconds: Seconds
    outcome: ImmediateScript


type ProviderScript = ImmediateScript | ScriptedDelay


@final
class ScriptedReviewerProvider:
    """Per-instance deterministic fake with concurrency observations."""

    __slots__ = (
        "_active_calls",
        "_call_count",
        "_delay",
        "_max_concurrency",
        "_requests",
        "_script",
    )

    _active_calls: int
    _call_count: int
    _delay: DelayController
    _max_concurrency: int
    _requests: list[ReviewerRequest]
    _script: tuple[ProviderScript, ...]

    def __init__(
        self,
        script: tuple[ProviderScript, ...],
        *,
        delay: DelayController | None = None,
    ) -> None:
        """Create a fake with one deterministic step per call."""
        self._script = script
        self._delay = AnyioDelayController() if delay is None else delay
        self._requests = []
        self._call_count = 0
        self._active_calls = 0
        self._max_concurrency = 0

    @property
    def call_count(self) -> int:
        """Return all attempted calls, including exhausted calls."""
        return self._call_count

    @property
    def active_calls(self) -> int:
        """Return calls currently resolving a configured script step."""
        return self._active_calls

    @property
    def max_concurrency(self) -> int:
        """Return maximum simultaneous configured calls observed."""
        return self._max_concurrency

    @property
    def requests(self) -> tuple[ReviewerRequest, ...]:
        """Return an immutable snapshot of captured requests."""
        return tuple(self._requests)

    async def review(self, request: ReviewerRequest) -> ReviewerResponse:
        """Resolve the next script step for a sanitized request."""
        call_number = self._call_count + 1
        self._call_count = call_number
        self._requests.append(request)
        if call_number > len(self._script):
            raise ScriptExhaustedError(call_number, len(self._script))

        script = self._script[call_number - 1]
        self._active_calls += 1
        self._max_concurrency = max(self._max_concurrency, self._active_calls)
        try:
            return await self._resolve(script)
        finally:
            self._active_calls -= 1

    async def _resolve(self, script: ProviderScript) -> ReviewerResponse:
        match script:
            case ScriptedDelay(seconds=seconds, outcome=outcome):
                await self._delay.wait(seconds)
                return self._resolve_immediate(outcome)
            case (
                ScriptedSuccess()
                | ScriptedMalformed()
                | ScriptedRefusal()
                | ScriptedTimeout()
                | ScriptedError()
            ):
                return self._resolve_immediate(script)
            case _:
                assert_never(script)

    @staticmethod
    def _resolve_immediate(script: ImmediateScript) -> ReviewerResponse:
        match script:
            case ScriptedSuccess(response=response):
                return response
            case ScriptedMalformed(raw_output=raw_output):
                return ReviewerResponse(raw_output=raw_output)
            case ScriptedRefusal(reason=reason):
                raise ProviderRefusalError(reason)
            case ScriptedTimeout(timeout_seconds=timeout_seconds):
                raise ProviderTimeoutError(timeout_seconds)
            case ScriptedError(detail=detail):
                raise ProviderCallError(detail)
            case _:
                assert_never(script)
