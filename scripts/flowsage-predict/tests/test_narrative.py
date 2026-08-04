import pytest

from flowsage_predict.narrative import (
    parse_narrative_text_tool_input,
    parse_node_insight_tool_input,
)


def test_parse_node_insight_tool_input() -> None:
    result = parse_node_insight_tool_input(
        {
            "insight": "Users abandon checkout because the total price is unclear.",
            "recommendations": [
                {
                    "title": "Show total upfront",
                    "description": "Display all fees before the final step.",
                    "expected_lift_pct": 12.0,
                }
            ],
        }
    )
    assert result.insight == "Users abandon checkout because the total price is unclear."
    assert result.recommendations[0].title == "Show total upfront"
    assert result.recommendations[0].expected_lift_pct == 12.0


def test_parse_node_insight_tool_input_allows_null_lift() -> None:
    result = parse_node_insight_tool_input(
        {
            "insight": "No abnormal signal.",
            "recommendations": [
                {"title": "t", "description": "d", "expected_lift_pct": None},
            ],
        }
    )
    assert result.recommendations[0].expected_lift_pct is None


def test_parse_narrative_text_tool_input() -> None:
    value = parse_narrative_text_tool_input(
        {"narrative": "Real users hesitated more than predicted."}, "narrative"
    )
    assert value == "Real users hesitated more than predicted."


def test_parse_narrative_text_tool_input_raises_on_missing_field() -> None:
    with pytest.raises(ValueError, match="Expected a string"):
        parse_narrative_text_tool_input({}, "narrative")


def test_parse_narrative_text_tool_input_raises_on_wrong_type() -> None:
    with pytest.raises(ValueError, match="Expected a string"):
        parse_narrative_text_tool_input({"rationale": 5}, "rationale")
