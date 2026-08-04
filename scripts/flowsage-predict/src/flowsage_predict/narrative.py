"""Text-only narrative client: turns funnel/calibration/retraining signal into
short AI-written analysis, via a forced tool call so parsing is deterministic.

Sibling to vision.py: same Protocol + real-Anthropic-implementation + fake-in-
tests split, just no image content block since these calls are text-only.
This module has zero flowsage-graph/flowsage-backend dependencies on purpose
(flowsage-predict is a standalone workspace package) -- FrictionSignal/
ScreenSignal are narrow local mirrors of flowsage_graph.models.FrictionNode
and flowsage_backend.calibration.ScreenCalibration, built by the caller.
"""

from __future__ import annotations

from typing import Protocol

import anthropic
from anthropic.types import MessageParam, ToolChoiceToolParam, ToolParam
from pydantic import BaseModel

NARRATIVE_MODEL = "claude-haiku-4-5-20251001"


class FrictionSignal(BaseModel):
    kind: str
    sessions_affected: int


class ScreenSignal(BaseModel):
    screen: str
    predicted_score: float
    observed_score: float
    delta: float


class NarrativeRecommendation(BaseModel):
    title: str
    description: str
    expected_lift_pct: float | None


class NodeInsightResult(BaseModel):
    insight: str
    recommendations: list[NarrativeRecommendation]


class NarrativeClient(Protocol):
    def generate_node_insight(
        self, screen: str, drop_off_rate: float, friction: list[FrictionSignal]
    ) -> NodeInsightResult: ...

    def generate_calibration_narrative(
        self, persona_name: str, anomalies: list[ScreenSignal]
    ) -> str: ...

    def generate_retraining_rationale(
        self,
        persona_name: str,
        anomalies: list[ScreenSignal],
        new_technical_literacy: float,
        new_anxiety: float,
        new_patience: float,
    ) -> str: ...


_NODE_INSIGHT_TOOL_NAME = "report_node_insight"
_NODE_INSIGHT_TOOL_SCHEMA: ToolParam = {
    "name": _NODE_INSIGHT_TOOL_NAME,
    "description": (
        "Report a usability insight and up to 3 re-engagement recommendations "
        "for a funnel screen."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "insight": {
                "type": "string",
                "description": (
                    "1-2 sentence plain-language explanation of the friction on " "this screen."
                ),
            },
            "recommendations": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "expected_lift_pct": {"type": ["number", "null"]},
                    },
                    "required": ["title", "description", "expected_lift_pct"],
                },
            },
        },
        "required": ["insight", "recommendations"],
    },
}
_NODE_INSIGHT_TOOL_CHOICE: ToolChoiceToolParam = {"type": "tool", "name": _NODE_INSIGHT_TOOL_NAME}

_CALIBRATION_NARRATIVE_TOOL_NAME = "report_calibration_narrative"
_CALIBRATION_NARRATIVE_TOOL_SCHEMA: ToolParam = {
    "name": _CALIBRATION_NARRATIVE_TOOL_NAME,
    "description": (
        "Explain in plain language why a persona's predicted friction diverged "
        "from what real users experienced."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": "1-3 sentence explanation of the divergence.",
            },
        },
        "required": ["narrative"],
    },
}
_CALIBRATION_NARRATIVE_TOOL_CHOICE: ToolChoiceToolParam = {
    "type": "tool",
    "name": _CALIBRATION_NARRATIVE_TOOL_NAME,
}

_RETRAINING_RATIONALE_TOOL_NAME = "report_retraining_rationale"
_RETRAINING_RATIONALE_TOOL_SCHEMA: ToolParam = {
    "name": _RETRAINING_RATIONALE_TOOL_NAME,
    "description": "Explain in plain language why a persona's sliders were adjusted.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": "1-3 sentence explanation of the slider adjustment.",
            },
        },
        "required": ["rationale"],
    },
}
_RETRAINING_RATIONALE_TOOL_CHOICE: ToolChoiceToolParam = {
    "type": "tool",
    "name": _RETRAINING_RATIONALE_TOOL_NAME,
}


def parse_node_insight_tool_input(tool_input: dict[str, object]) -> NodeInsightResult:
    """Validate a tool-call payload from Claude into a `NodeInsightResult`.

    Extracted so unit tests can exercise parsing/validation without a network call.
    """
    return NodeInsightResult.model_validate(tool_input)


def parse_narrative_text_tool_input(tool_input: dict[str, object], field: str) -> str:
    """Validate a single-string-field tool-call payload (calibration narrative,
    retraining rationale both use this shape)."""
    value = tool_input.get(field)
    if not isinstance(value, str):
        raise ValueError(f"Expected a string {field!r} field, got: {tool_input!r}")
    return value


def _screens_summary(anomalies: list[ScreenSignal]) -> str:
    return "\n".join(
        f"- {a.screen}: predicted {a.predicted_score:.2f}, observed "
        f"{a.observed_score:.2f} (delta {a.delta:+.2f})"
        for a in anomalies
    )


class AnthropicNarrativeClient:
    """Calls the Anthropic Messages API with a forced tool call, text-only (no
    image content block, unlike `vision.AnthropicVisionClient`)."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic()

    def generate_node_insight(
        self, screen: str, drop_off_rate: float, friction: list[FrictionSignal]
    ) -> NodeInsightResult:
        friction_summary = (
            "\n".join(f"- {f.kind} affecting {f.sessions_affected} sessions" for f in friction)
            or "- no specific friction pattern recorded"
        )
        message: MessageParam = {
            "role": "user",
            "content": (
                f"Screen '{screen}' has a {drop_off_rate * 100:.0f}% drop-off rate. "
                f"Detected friction patterns:\n{friction_summary}\n\n"
                "Explain the likely usability problem in plain language and suggest "
                "up to 3 concrete fixes ranked by expected impact."
            ),
        }
        response = self._client.messages.create(
            model=NARRATIVE_MODEL,
            max_tokens=512,
            tools=[_NODE_INSIGHT_TOOL_SCHEMA],
            tool_choice=_NODE_INSIGHT_TOOL_CHOICE,
            messages=[message],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == _NODE_INSIGHT_TOOL_NAME:
                tool_input = block.input
                assert isinstance(tool_input, dict)
                return parse_node_insight_tool_input(tool_input)
        raise RuntimeError(f"Claude did not call {_NODE_INSIGHT_TOOL_NAME!r}")

    def generate_calibration_narrative(
        self, persona_name: str, anomalies: list[ScreenSignal]
    ) -> str:
        message: MessageParam = {
            "role": "user",
            "content": (
                f"Persona '{persona_name}' predicted friction that diverged from real "
                f"user behavior on these screens:\n{_screens_summary(anomalies)}\n\n"
                "In 1-3 sentences, explain the likely reason predicted and observed "
                "friction diverged."
            ),
        }
        response = self._client.messages.create(
            model=NARRATIVE_MODEL,
            max_tokens=256,
            tools=[_CALIBRATION_NARRATIVE_TOOL_SCHEMA],
            tool_choice=_CALIBRATION_NARRATIVE_TOOL_CHOICE,
            messages=[message],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == _CALIBRATION_NARRATIVE_TOOL_NAME:
                tool_input = block.input
                assert isinstance(tool_input, dict)
                return parse_narrative_text_tool_input(tool_input, "narrative")
        raise RuntimeError(f"Claude did not call {_CALIBRATION_NARRATIVE_TOOL_NAME!r}")

    def generate_retraining_rationale(
        self,
        persona_name: str,
        anomalies: list[ScreenSignal],
        new_technical_literacy: float,
        new_anxiety: float,
        new_patience: float,
    ) -> str:
        message: MessageParam = {
            "role": "user",
            "content": (
                f"Persona '{persona_name}' was just retrained from observed behavioral "
                f"evidence on these anomalous screens:\n{_screens_summary(anomalies)}\n\n"
                f"New sliders -- technical_literacy={new_technical_literacy:.2f}, "
                f"anxiety={new_anxiety:.2f}, patience={new_patience:.2f}. In 1-3 "
                "sentences, explain in plain language why this adjustment makes sense."
            ),
        }
        response = self._client.messages.create(
            model=NARRATIVE_MODEL,
            max_tokens=256,
            tools=[_RETRAINING_RATIONALE_TOOL_SCHEMA],
            tool_choice=_RETRAINING_RATIONALE_TOOL_CHOICE,
            messages=[message],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == _RETRAINING_RATIONALE_TOOL_NAME:
                tool_input = block.input
                assert isinstance(tool_input, dict)
                return parse_narrative_text_tool_input(tool_input, "rationale")
        raise RuntimeError(f"Claude did not call {_RETRAINING_RATIONALE_TOOL_NAME!r}")
