from datetime import datetime, timedelta, timezone

from flowsage_graph.funnel import detect_friction, discover_funnel
from flowsage_graph.models import Event, FrictionKind

_T0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)


def _event(session: str, screen: str, seconds: float, event: str = "screen_view") -> Event:
    return Event(
        session_id=session,
        screen=screen,
        event=event,
        timestamp=_T0 + timedelta(seconds=seconds),
        device="mobile",
        cohort="paid",
    )


def _linear_session(session: str, screens: list[str], start: float = 0) -> list[Event]:
    return [_event(session, screen, start + i * 10) for i, screen in enumerate(screens)]


def test_discover_funnel_picks_the_most_traveled_path() -> None:
    events = [
        *_linear_session("s1", ["landing", "cart", "checkout", "confirm"]),
        *_linear_session("s2", ["landing", "cart", "checkout"]),
        *_linear_session("s3", ["landing", "cart"]),
        *_linear_session("s4", ["landing", "cart", "checkout", "confirm"]),
    ]
    funnel = discover_funnel(events)
    assert [step.screen for step in funnel] == ["landing", "cart", "checkout", "confirm"]

    by_screen = {step.screen: step for step in funnel}
    assert by_screen["landing"].sessions_entered == 4
    assert by_screen["landing"].drop_off_rate == 0.0
    assert by_screen["cart"].sessions_entered == 4
    assert by_screen["cart"].sessions_continued == 3
    assert by_screen["checkout"].sessions_entered == 3
    assert by_screen["checkout"].sessions_continued == 2
    assert by_screen["confirm"].sessions_entered == 2
    assert by_screen["confirm"].drop_off_rate == 0.0


def test_discover_funnel_returns_empty_for_no_events() -> None:
    assert discover_funnel([]) == []


def test_detect_friction_flags_abnormal_drop_off() -> None:
    events = [
        *_linear_session("s1", ["landing", "cart", "checkout", "confirm"]),
        *_linear_session("s2", ["landing", "cart", "checkout"]),
        *_linear_session("s3", ["landing", "cart", "checkout"]),
        *_linear_session("s4", ["landing", "cart", "checkout"]),
    ]
    funnel = discover_funnel(events)
    friction = detect_friction(events, funnel)

    drop_off_nodes = [f for f in friction if f.kind == FrictionKind.ABNORMAL_DROP_OFF]
    assert any(node.screen == "checkout" for node in drop_off_nodes)


def test_detect_friction_flags_rage_loop() -> None:
    events = [
        _event("s1", "landing", 0),
        _event("s1", "checkout", 10, "click"),
        _event("s1", "checkout", 11, "click"),
        _event("s1", "checkout", 12, "click"),
    ]
    friction = detect_friction(events, discover_funnel(events))

    rage_nodes = [f for f in friction if f.kind == FrictionKind.RAGE_LOOP]
    assert len(rage_nodes) == 1
    assert rage_nodes[0].screen == "checkout"
    assert rage_nodes[0].sessions_affected == 1


def test_detect_friction_flags_backtrack() -> None:
    events = [
        _event("s1", "landing", 0),
        _event("s1", "cart", 10),
        _event("s1", "checkout", 20),
        _event("s1", "cart", 30),  # backtracks after reaching checkout
    ]
    funnel = discover_funnel(events)
    friction = detect_friction(events, funnel)

    backtrack_nodes = [f for f in friction if f.kind == FrictionKind.BACKTRACK]
    assert len(backtrack_nodes) == 1
    assert backtrack_nodes[0].screen == "cart"


def test_detect_friction_returns_empty_for_clean_funnel() -> None:
    events = [
        *_linear_session("s1", ["landing", "cart", "checkout", "confirm"]),
        *_linear_session("s2", ["landing", "cart", "checkout", "confirm"]),
    ]
    funnel = discover_funnel(events)
    assert detect_friction(events, funnel) == []
