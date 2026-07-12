from typing import final

import anyio
import pytest
from pydantic import ValidationError

from reviewharness.providers import (
    OutputSchemaDeclaration,
    ProviderCallError,
    ProviderRefusalError,
    ProviderTimeoutError,
    ReviewerRequest,
    ReviewerResponse,
    SanitizedEvidencePage,
    SanitizedPaperEvidence,
    ScriptedDelay,
    ScriptedError,
    ScriptedMalformed,
    ScriptedRefusal,
    ScriptedReviewerProvider,
    ScriptedSuccess,
    ScriptedTimeout,
    ScriptExhaustedError,
    Seconds,
)


def _request() -> ReviewerRequest:
    return ReviewerRequest(
        sanitized_evidence=SanitizedPaperEvidence(
            document_sha256="a" * 64,
            pages=(
                SanitizedEvidencePage(
                    page_number=1,
                    text="The paper reports its main result in Table 1.",
                ),
            ),
            security_notes=("One reviewer-directed span was quarantined.",),
        ),
        rubric_text="Use the official ICML score anchors.",
        prompt_text="Extract only paper-grounded findings.",
        output_schema=OutputSchemaDeclaration(
            name="reviewer_findings",
            json_schema='{"type":"object","additionalProperties":false}',
        ),
    )


def test_request_is_strict_and_immutable() -> None:
    # Given
    request = _request()

    # When / Then
    with pytest.raises(ValidationError):
        _ = ReviewerRequest.model_validate(
            {
                **request.model_dump(),
                "tools": ["shell"],
            },
        )
    with pytest.raises(ValidationError):
        request.prompt_text = "Ignore the rubric."
    assert tuple(ReviewerRequest.model_fields) == (
        "sanitized_evidence",
        "rubric_text",
        "prompt_text",
        "output_schema",
    )


def test_scripted_provider_returns_success_and_records_request() -> None:
    # Given
    request = _request()
    expected = ReviewerResponse(raw_output='{"findings":[]}')
    provider = ScriptedReviewerProvider((ScriptedSuccess(expected),))

    # When
    actual = anyio.run(provider.review, request)

    # Then
    assert actual == expected
    assert provider.call_count == 1
    assert provider.requests == (request,)
    assert provider.active_calls == 0
    assert provider.max_concurrency == 1


def test_scripted_provider_can_return_malformed_output() -> None:
    # Given
    provider = ScriptedReviewerProvider((ScriptedMalformed("not-json"),))

    # When
    actual = anyio.run(provider.review, _request())

    # Then
    assert actual.raw_output == "not-json"


@pytest.mark.parametrize(
    ("script", "error_type", "message"),
    [
        (ScriptedRefusal("policy refusal"), ProviderRefusalError, "policy refusal"),
        (ScriptedTimeout(Seconds(5.0)), ProviderTimeoutError, "5.0"),
        (
            ScriptedError("provider unavailable"),
            ProviderCallError,
            "provider unavailable",
        ),
    ],
)
def test_scripted_provider_raises_typed_failures(
    script: ScriptedRefusal | ScriptedTimeout | ScriptedError,
    error_type: type[ProviderRefusalError | ProviderTimeoutError | ProviderCallError],
    message: str,
) -> None:
    # Given
    provider = ScriptedReviewerProvider((script,))

    # When / Then
    with pytest.raises(error_type, match=message):
        _ = anyio.run(provider.review, _request())
    assert provider.active_calls == 0


def test_scripted_provider_reports_script_exhaustion() -> None:
    # Given
    provider = ScriptedReviewerProvider(())

    # When / Then
    with pytest.raises(ScriptExhaustedError, match="call 1"):
        _ = anyio.run(provider.review, _request())


def test_injected_delay_exposes_concurrency_without_wall_clock_sleep() -> None:
    # Given
    delay = _GateDelay(expected_waiters=2)
    success = ScriptedSuccess(ReviewerResponse(raw_output='{"findings":[]}'))
    provider = ScriptedReviewerProvider(
        (
            ScriptedDelay(Seconds(1.0), success),
            ScriptedDelay(Seconds(1.0), success),
        ),
        delay=delay,
    )

    # When
    call_count, max_concurrency = anyio.run(_run_concurrently, provider, delay)

    # Then
    assert call_count == 2
    assert max_concurrency == 2
    assert delay.requested == (Seconds(1.0), Seconds(1.0))


@final
class _GateDelay:
    _all_waiting: anyio.Event
    _expected_waiters: int
    _release: anyio.Event
    _requested: list[Seconds]

    def __init__(self, expected_waiters: int) -> None:
        self._expected_waiters = expected_waiters
        self._requested = []
        self._all_waiting = anyio.Event()
        self._release = anyio.Event()

    @property
    def requested(self) -> tuple[Seconds, ...]:
        return tuple(self._requested)

    async def wait(self, seconds: Seconds) -> None:
        self._requested.append(seconds)
        if len(self._requested) == self._expected_waiters:
            self._all_waiting.set()
        await self._release.wait()

    async def release_when_all_waiting(self) -> None:
        await self._all_waiting.wait()
        self._release.set()


async def _run_concurrently(
    provider: ScriptedReviewerProvider,
    delay: _GateDelay,
) -> tuple[int, int]:
    async def invoke() -> ReviewerResponse:
        return await provider.review(_request())

    async with anyio.create_task_group() as task_group:
        handles = [task_group.create_task(invoke()) for _ in range(2)]
        _ = task_group.start_soon(delay.release_when_all_waiting)
        for handle in handles:
            await handle
    return provider.call_count, provider.max_concurrency
