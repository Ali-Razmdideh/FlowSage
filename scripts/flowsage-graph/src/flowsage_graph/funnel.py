"""Automatic funnel discovery and friction-node detection from a raw event log.

No funnel is manually defined (README §Features: "automatic funnel discovery, no
manual funnel definitions"). Instead we find the most-traveled path through the
observed screen-to-screen transitions and measure drop-off along it, then flag
three friction patterns: abnormal drop-off, rage-loops, and backtracking.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from flowsage_graph.ingest import session_transitions
from flowsage_graph.models import Event, FrictionKind, FrictionNode, FunnelStep

DEFAULT_DROP_OFF_THRESHOLD = 0.5
DEFAULT_RAGE_LOOP_THRESHOLD = 3


def _group_sorted_by_session(events: list[Event]) -> dict[str, list[Event]]:
    by_session: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        by_session[event.session_id].append(event)
    return {
        session_id: sorted(session_events, key=lambda e: e.timestamp)
        for session_id, session_events in by_session.items()
    }


def _session_reaches_after(session_events: list[Event], screen: str, next_screen: str) -> bool:
    reached_screen = False
    for event in session_events:
        if event.screen == screen:
            reached_screen = True
            continue
        if reached_screen and event.screen == next_screen:
            return True
    return False


def discover_funnel(events: list[Event]) -> list[FunnelStep]:
    """Greedily follow the heaviest outgoing transition from the most common entry screen."""
    by_session = _group_sorted_by_session(events)
    if not by_session:
        return []

    entry_counts = Counter(session[0].screen for session in by_session.values())
    start_screen = entry_counts.most_common(1)[0][0]

    transition_counts: Counter[tuple[str, str]] = Counter()
    for prev, curr in session_transitions(events):
        transition_counts[(prev.screen, curr.screen)] += 1

    path = [start_screen]
    visited = {start_screen}
    current = start_screen
    while True:
        candidates = [
            (to_screen, count)
            for (from_screen, to_screen), count in transition_counts.items()
            if from_screen == current and to_screen not in visited
        ]
        if not candidates:
            break
        next_screen = max(candidates, key=lambda candidate: candidate[1])[0]
        path.append(next_screen)
        visited.add(next_screen)
        current = next_screen

    steps: list[FunnelStep] = []
    for i, screen in enumerate(path):
        sessions_with_screen = {
            session_id
            for session_id, session_events in by_session.items()
            if any(e.screen == screen for e in session_events)
        }
        if i + 1 < len(path):
            next_screen = path[i + 1]
            sessions_continued = {
                session_id
                for session_id in sessions_with_screen
                if _session_reaches_after(by_session[session_id], screen, next_screen)
            }
        else:
            sessions_continued = sessions_with_screen
        steps.append(
            FunnelStep(
                screen=screen,
                sessions_entered=len(sessions_with_screen),
                sessions_continued=len(sessions_continued),
            )
        )
    return steps


def detect_friction(
    events: list[Event],
    funnel: list[FunnelStep],
    *,
    drop_off_threshold: float = DEFAULT_DROP_OFF_THRESHOLD,
    rage_loop_threshold: int = DEFAULT_RAGE_LOOP_THRESHOLD,
) -> list[FrictionNode]:
    by_session = _group_sorted_by_session(events)
    friction: list[FrictionNode] = []

    for step in funnel:
        if step.sessions_entered > 0 and step.drop_off_rate >= drop_off_threshold:
            friction.append(
                FrictionNode(
                    screen=step.screen,
                    kind=FrictionKind.ABNORMAL_DROP_OFF,
                    detail=(
                        f"{step.drop_off_rate:.0%} of sessions that reached "
                        f"'{step.screen}' never continued."
                    ),
                    sessions_affected=step.sessions_entered - step.sessions_continued,
                )
            )

    rage_counts: dict[str, int] = defaultdict(int)
    for session_events in by_session.values():
        run_screen: str | None = None
        run_length = 0
        flagged_this_session: set[str] = set()
        for event in session_events:
            if event.screen == run_screen:
                run_length += 1
            else:
                run_screen = event.screen
                run_length = 1
            if run_length >= rage_loop_threshold and run_screen not in flagged_this_session:
                rage_counts[run_screen] += 1
                flagged_this_session.add(run_screen)

    for screen, sessions_affected in rage_counts.items():
        friction.append(
            FrictionNode(
                screen=screen,
                kind=FrictionKind.RAGE_LOOP,
                detail=(
                    f"{sessions_affected} session(s) repeated {rage_loop_threshold}+ actions "
                    f"on '{screen}' without progressing."
                ),
                sessions_affected=sessions_affected,
            )
        )

    path_index = {step.screen: i for i, step in enumerate(funnel)}
    backtrack_counts: dict[str, int] = defaultdict(int)
    for session_events in by_session.values():
        max_index_seen = -1
        flagged_this_session = set()
        for event in session_events:
            idx = path_index.get(event.screen)
            if idx is None:
                continue
            if idx < max_index_seen and event.screen not in flagged_this_session:
                backtrack_counts[event.screen] += 1
                flagged_this_session.add(event.screen)
            max_index_seen = max(max_index_seen, idx)

    for screen, sessions_affected in backtrack_counts.items():
        friction.append(
            FrictionNode(
                screen=screen,
                kind=FrictionKind.BACKTRACK,
                detail=(
                    f"{sessions_affected} session(s) backtracked to '{screen}' after moving "
                    "further along the funnel."
                ),
                sessions_affected=sessions_affected,
            )
        )

    return friction
