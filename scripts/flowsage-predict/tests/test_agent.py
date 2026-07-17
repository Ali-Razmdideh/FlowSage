from pathlib import Path

import pytest

from flowsage_predict.agent import run_persona_walkthrough
from flowsage_predict.models import (
    BehavioralSliders,
    DemographicAnchors,
    FrictionIssue,
    Persona,
    ScreenEvaluation,
    Severity,
    SimulationStep,
)


def _persona() -> Persona:
    return Persona(
        id="novice",
        name="Novice User",
        description="Test persona",
        demographic_anchors=DemographicAnchors(
            tech_affinity="low", primary_device="mobile", discovery_mode="search-driven"
        ),
        sliders=BehavioralSliders(technical_literacy=0.2, anxiety=0.8, patience=0.3, curiosity=0.4),
    )


class ScriptedVisionClient:
    """Fake VisionClient that plays back one evaluation per call, in order."""

    def __init__(self, evaluations: list[ScreenEvaluation]) -> None:
        self._evaluations = evaluations
        self.calls: list[tuple[str, list[SimulationStep]]] = []

    def evaluate_screen(
        self,
        persona: Persona,
        goal: str,
        screenshot: Path,
        history: list[SimulationStep],
    ) -> ScreenEvaluation:
        self.calls.append((screenshot.stem, list(history)))
        return self._evaluations[len(self.calls) - 1]


def test_walkthrough_completes_all_screens_when_never_abandoning() -> None:
    screenshots = [Path("01_cart.png"), Path("02_shipping.png"), Path("03_confirm.png")]
    evaluations = [
        ScreenEvaluation(action="Adds item to cart", reasoning="Wants to buy it"),
        ScreenEvaluation(action="Enters shipping address", reasoning="Required to continue"),
        ScreenEvaluation(action="Confirms order", reasoning="Sees the total and confirms"),
    ]
    client = ScriptedVisionClient(evaluations)

    result = run_persona_walkthrough(
        persona=_persona(), goal="Complete purchase", screenshots=screenshots, vision_client=client
    )

    assert result["index"] == 3
    assert result["done"] is True
    assert [s.screen for s in result["steps"]] == ["01_cart", "02_shipping", "03_confirm"]
    assert result["issues"] == []
    assert len(client.calls) == 3


def test_walkthrough_stops_early_on_abandon() -> None:
    screenshots = [Path("01_cart.png"), Path("02_shipping.png"), Path("03_confirm.png")]
    friction = FrictionIssue(
        screen="02_shipping",
        severity=Severity.HIGH,
        title="Zip code validation rejects valid input",
        heuristic_violated="Error Prevention",
        persona_impact="Novice user cannot proceed",
        description="Regex rejects zip+4 format",
        suggested_fix="Accept zip+4 or explain the expected format",
    )
    evaluations = [
        ScreenEvaluation(action="Adds item to cart", reasoning="Wants to buy it"),
        ScreenEvaluation(
            action="Gives up after repeated validation errors",
            reasoning="Cannot figure out the expected zip code format",
            abandon=True,
            friction=friction,
        ),
    ]
    client = ScriptedVisionClient(evaluations)

    result = run_persona_walkthrough(
        persona=_persona(), goal="Complete purchase", screenshots=screenshots, vision_client=client
    )

    assert result["done"] is True
    assert len(result["steps"]) == 2
    assert result["index"] == 2  # reached screen 3 but never evaluated it
    assert len(client.calls) == 2
    assert result["issues"] == [friction]


def test_walkthrough_history_passed_to_client_grows_each_call() -> None:
    screenshots = [Path("01.png"), Path("02.png")]
    evaluations = [
        ScreenEvaluation(action="a1", reasoning="r1"),
        ScreenEvaluation(action="a2", reasoning="r2"),
    ]
    client = ScriptedVisionClient(evaluations)

    run_persona_walkthrough(
        persona=_persona(), goal="goal", screenshots=screenshots, vision_client=client
    )

    assert client.calls[0][1] == []
    assert len(client.calls[1][1]) == 1
    assert client.calls[1][1][0].screen == "01"


def test_walkthrough_requires_at_least_one_screenshot() -> None:
    with pytest.raises(ValueError, match="At least one screenshot"):
        run_persona_walkthrough(
            persona=_persona(), goal="goal", screenshots=[], vision_client=ScriptedVisionClient([])
        )
