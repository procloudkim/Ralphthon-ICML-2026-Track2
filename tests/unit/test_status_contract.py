from reviewharness.runbook_adapter import (
    AuthenticatedStatus,
    GuidanceReasonCode,
)


def test_status_accepts_null_windows_and_missing_counts() -> None:
    status = AuthenticatedStatus.model_validate(
        {
            "guidance": {
                "stage": "track2_review",
                "action_available": False,
                "reason_code": "all_reviews_submitted",
                "next_action": "none",
                "next_action_actor": "agent",
                "time": {
                    "timezone": "Asia/Seoul",
                    "now": "2026-07-12T16:50:00+09:00",
                    "window_opens_at": None,
                    "window_closes_at": None,
                },
                "prerequisites": [
                    {
                        "code": "track2_report",
                        "satisfied": True,
                        "actor": "server",
                    }
                ],
                "future_guidance_field": "ignored",
            },
            "future_status_field": "ignored",
        }
    )

    assert status.assigned is None
    assert status.submitted is None
    assert status.remaining is None
    assert status.guidance.reason_code is GuidanceReasonCode.ALL_REVIEWS_SUBMITTED
    assert status.guidance.time.window_opens_at is None
    assert status.guidance.time.window_closes_at is None
    assert status.guidance.prerequisites[0].code == "track2_report"
    assert status.guidance.prerequisites[0].satisfied is True
    assert status.guidance.prerequisites[0].actor == "server"


def test_status_accepts_legacy_string_prerequisites() -> None:
    status = AuthenticatedStatus.model_validate(
        {
            "guidance": {
                "stage": "track2_review",
                "action_available": True,
                "reason_code": "reviews_remaining",
                "next_action": "review_assignments",
                "next_action_actor": "agent",
                "time": {
                    "timezone": "Asia/Seoul",
                    "now": "2026-07-12T16:50:00+09:00",
                    "window_opens_at": "2026-07-12T16:45:00+09:00",
                    "window_closes_at": "2026-07-12T17:00:00+09:00",
                },
                "prerequisites": ["setup_token_exchanged", "track2_window_open"],
            },
        }
    )

    assert status.guidance.prerequisites[0].code == "setup_token_exchanged"
    assert status.guidance.prerequisites[0].satisfied is True
    assert status.guidance.prerequisites[0].actor == "server"
    assert status.guidance.prerequisites[1].code == "track2_window_open"
