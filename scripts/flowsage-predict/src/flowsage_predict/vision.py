"""Vision client abstraction: turns one screenshot + persona context into a `ScreenEvaluation`.

`AnthropicVisionClient` is the production implementation, calling Claude with a forced
tool call so the response is always well-formed JSON. Tests use a fake implementing the
same `VisionClient` protocol, so the LangGraph agent in `agent.py` never talks to the
network directly.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Literal, Protocol

import anthropic
from anthropic.types import (
    Base64ImageSourceParam,
    ImageBlockParam,
    MessageParam,
    TextBlockParam,
    ToolChoiceToolParam,
    ToolParam,
)

from flowsage_predict.models import Persona, ScreenEvaluation, SimulationStep

_EVALUATION_TOOL_NAME = "report_screen_evaluation"

_EVALUATION_TOOL_SCHEMA: ToolParam = {
    "name": _EVALUATION_TOOL_NAME,
    "description": (
        "Report what the persona does on this screen and any usability friction it experiences."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "The single action the persona takes on this screen.",
            },
            "reasoning": {
                "type": "string",
                "description": "Why the persona chose that action, in character.",
            },
            "abandon": {
                "type": "boolean",
                "description": "True if the persona gives up and leaves the flow here.",
            },
            "friction": {
                "type": ["object", "null"],
                "description": "A usability issue the persona hit on this screen, or null.",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "title": {"type": "string"},
                    "heuristic_violated": {"type": "string"},
                    "persona_impact": {"type": "string"},
                    "description": {"type": "string"},
                    "suggested_fix": {"type": "string"},
                },
                "required": [
                    "severity",
                    "title",
                    "heuristic_violated",
                    "persona_impact",
                    "description",
                    "suggested_fix",
                ],
            },
        },
        "required": ["action", "reasoning", "abandon"],
    },
}

_EVALUATION_TOOL_CHOICE: ToolChoiceToolParam = {"type": "tool", "name": _EVALUATION_TOOL_NAME}

_ImageMediaType = Literal["image/png", "image/jpeg", "image/webp"]

_IMAGE_MEDIA_TYPES: dict[str, _ImageMediaType] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class VisionClient(Protocol):
    def evaluate_screen(
        self,
        persona: Persona,
        goal: str,
        screenshot: Path,
        history: list[SimulationStep],
    ) -> ScreenEvaluation: ...


def _history_summary(history: list[SimulationStep]) -> str:
    if not history:
        return "This is the first screen; there is no prior history."
    lines = [f"{i + 1}. On '{step.screen}': {step.action}" for i, step in enumerate(history)]
    return "Prior steps taken so far:\n" + "\n".join(lines)


def _media_type_for(path: Path) -> _ImageMediaType:
    media_type = _IMAGE_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise ValueError(
            f"Unsupported screenshot format {path.suffix!r} for {path}; "
            f"expected one of {sorted(_IMAGE_MEDIA_TYPES)}"
        )
    return media_type


def parse_evaluation_tool_input(
    tool_input: dict[str, object], screen_name: str
) -> ScreenEvaluation:
    """Validate a tool-call payload from Claude into a `ScreenEvaluation`.

    Extracted so unit tests can exercise parsing/validation without a network call.
    """
    friction = tool_input.get("friction")
    payload = dict(tool_input)
    if isinstance(friction, dict):
        payload["friction"] = {"screen": screen_name, **friction}
    return ScreenEvaluation.model_validate(payload)


class AnthropicVisionClient:
    """Calls the Anthropic Messages API with vision input and a forced tool call."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic()

    def evaluate_screen(
        self,
        persona: Persona,
        goal: str,
        screenshot: Path,
        history: list[SimulationStep],
    ) -> ScreenEvaluation:
        image_bytes = screenshot.read_bytes()
        encoded = base64.standard_b64encode(image_bytes).decode("ascii")

        text_block: TextBlockParam = {
            "type": "text",
            "text": (
                f"Goal for this walkthrough: {goal}\n\n"
                f"{_history_summary(history)}\n\n"
                "Here is the current screen. Decide what you, as this persona, "
                "do next, and report any friction you experience."
            ),
        }
        image_source: Base64ImageSourceParam = {
            "type": "base64",
            "media_type": _media_type_for(screenshot),
            "data": encoded,
        }
        image_block: ImageBlockParam = {"type": "image", "source": image_source}
        message: MessageParam = {
            "role": "user",
            "content": [text_block, image_block],
        }

        response = self._client.messages.create(
            model=persona.model,
            max_tokens=1024,
            system=persona.system_prompt(),
            tools=[_EVALUATION_TOOL_SCHEMA],
            tool_choice=_EVALUATION_TOOL_CHOICE,
            messages=[message],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == _EVALUATION_TOOL_NAME:
                tool_input = block.input
                assert isinstance(tool_input, dict)
                return parse_evaluation_tool_input(tool_input, screen_name=screenshot.stem)

        raise RuntimeError(
            f"Claude did not call {_EVALUATION_TOOL_NAME!r}; got blocks: "
            f"{json.dumps([b.type for b in response.content])}"
        )
