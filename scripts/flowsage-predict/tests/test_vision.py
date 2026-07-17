from pathlib import Path

import pytest

from flowsage_predict.models import SimulationStep
from flowsage_predict.vision import _history_summary, _media_type_for, parse_evaluation_tool_input


def test_parse_evaluation_tool_input_without_friction() -> None:
    evaluation = parse_evaluation_tool_input(
        {"action": "Taps continue", "reasoning": "Looks like the next step", "abandon": False},
        screen_name="cart",
    )
    assert evaluation.action == "Taps continue"
    assert evaluation.friction is None
    assert evaluation.abandon is False


def test_parse_evaluation_tool_input_with_friction_stamps_screen_name() -> None:
    evaluation = parse_evaluation_tool_input(
        {
            "action": "Gives up",
            "reasoning": "Error message is unreadable",
            "abandon": True,
            "friction": {
                "severity": "critical",
                "title": "Unreadable error",
                "heuristic_violated": "Error Prevention",
                "persona_impact": "Blocks completion entirely",
                "description": "Contrast ratio 2.8:1",
                "suggested_fix": "Raise contrast to 4.5:1",
            },
        },
        screen_name="payment",
    )
    assert evaluation.friction is not None
    assert evaluation.friction.screen == "payment"
    assert evaluation.friction.severity.value == "critical"


def test_history_summary_empty() -> None:
    assert "no prior history" in _history_summary([])


def test_history_summary_lists_prior_steps() -> None:
    steps = [
        SimulationStep(screen="cart", action="Adds item", reasoning="Wants to buy it"),
        SimulationStep(screen="checkout", action="Enters address", reasoning="Required field"),
    ]
    summary = _history_summary(steps)
    assert "1. On 'cart': Adds item" in summary
    assert "2. On 'checkout': Enters address" in summary


def test_media_type_for_known_extensions() -> None:
    assert _media_type_for(Path("shot.png")) == "image/png"
    assert _media_type_for(Path("shot.JPG")) == "image/jpeg"


def test_media_type_for_unsupported_extension_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported screenshot format"):
        _media_type_for(Path("shot.gif"))
