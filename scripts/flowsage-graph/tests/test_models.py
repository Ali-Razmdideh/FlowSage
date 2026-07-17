from datetime import datetime, timezone

from flowsage_graph.models import FunnelStep


def test_funnel_step_drop_off_rate() -> None:
    step = FunnelStep(screen="cart", sessions_entered=100, sessions_continued=60)
    assert step.drop_off_rate == 0.4


def test_funnel_step_drop_off_rate_handles_zero_entered() -> None:
    step = FunnelStep(screen="cart", sessions_entered=0, sessions_continued=0)
    assert step.drop_off_rate == 0.0


def test_event_parses_iso_timestamp_with_z_suffix() -> None:
    from flowsage_graph.models import Event

    event = Event.model_validate(
        {
            "session_id": "s1",
            "screen": "cart",
            "event": "screen_view",
            "timestamp": "2026-07-17T14:02:45Z",
        }
    )
    assert event.timestamp.tzinfo is not None
    assert event.timestamp.astimezone(timezone.utc) == datetime(
        2026, 7, 17, 14, 2, 45, tzinfo=timezone.utc
    )
