"""LangGraph agent that walks a persona through a screenshot sequence.

Graph shape: a single `evaluate` node loops on itself (one iteration per screenshot),
calling out to a `VisionClient` to decide the persona's action and any friction hit,
until the persona abandons the flow or the screenshots run out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterator, TypedDict, cast

from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from flowsage_predict.models import FrictionIssue, Persona, SimulationStep
from flowsage_predict.vision import VisionClient


class AgentState(TypedDict):
    persona: Persona
    goal: str
    screenshots: list[Path]
    index: int
    steps: list[SimulationStep]
    issues: list[FrictionIssue]
    done: bool


def _make_evaluate_node(vision_client: VisionClient) -> Callable[[AgentState], AgentState]:
    def evaluate(state: AgentState) -> AgentState:
        screenshot = state["screenshots"][state["index"]]
        evaluation = vision_client.evaluate_screen(
            persona=state["persona"],
            goal=state["goal"],
            screenshot=screenshot,
            history=state["steps"],
        )
        step = SimulationStep(
            screen=screenshot.stem,
            action=evaluation.action,
            reasoning=evaluation.reasoning,
            friction=evaluation.friction,
        )
        steps = [*state["steps"], step]
        issues = [*state["issues"], evaluation.friction] if evaluation.friction else state["issues"]
        next_index = state["index"] + 1
        reached_end = next_index >= len(state["screenshots"])
        return {
            **state,
            "steps": steps,
            "issues": issues,
            "index": next_index,
            "done": evaluation.abandon or reached_end,
        }

    return evaluate


def _route(state: AgentState) -> str:
    return "end" if state["done"] else "continue"


def build_agent_graph(vision_client: VisionClient) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("evaluate", _make_evaluate_node(vision_client))
    graph.set_entry_point("evaluate")
    graph.add_conditional_edges("evaluate", _route, {"continue": "evaluate", "end": END})
    return graph.compile()


def _initial_state(persona: Persona, goal: str, screenshots: list[Path]) -> AgentState:
    return {
        "persona": persona,
        "goal": goal,
        "screenshots": screenshots,
        "index": 0,
        "steps": [],
        "issues": [],
        "done": False,
    }


def _recursion_config(screenshot_count: int) -> RunnableConfig:
    # +5 headroom over one node-visit per screenshot for LangGraph's internal bookkeeping.
    return {"recursion_limit": screenshot_count + 5}


def run_persona_walkthrough(
    persona: Persona,
    goal: str,
    screenshots: list[Path],
    vision_client: VisionClient,
) -> AgentState:
    """Run the full walkthrough synchronously and return the final agent state."""
    if not screenshots:
        raise ValueError("At least one screenshot is required")

    graph = build_agent_graph(vision_client)
    initial_state = _initial_state(persona, goal, screenshots)
    config = _recursion_config(len(screenshots))
    return cast(AgentState, graph.invoke(initial_state, config=config))


def iter_persona_walkthrough(
    persona: Persona,
    goal: str,
    screenshots: list[Path],
    vision_client: VisionClient,
) -> Iterator[AgentState]:
    """Run the walkthrough, yielding the state after each screen is evaluated.

    Lets a caller (e.g. a job worker) persist/broadcast each step as it happens,
    instead of waiting for the whole walkthrough to finish like
    `run_persona_walkthrough` does.
    """
    if not screenshots:
        raise ValueError("At least one screenshot is required")

    graph = build_agent_graph(vision_client)
    initial_state = _initial_state(persona, goal, screenshots)
    config = _recursion_config(len(screenshots))
    for state in graph.stream(initial_state, config=config, stream_mode="values"):
        yield cast(AgentState, state)
