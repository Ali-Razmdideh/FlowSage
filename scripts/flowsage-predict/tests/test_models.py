import pytest
from pydantic import ValidationError

from flowsage_predict.models import (
    BehavioralSliders,
    DemographicAnchors,
    FrictionIssue,
    Persona,
    Severity,
)


def _make_persona(**slider_overrides: float) -> Persona:
    sliders = {"technical_literacy": 0.5, "anxiety": 0.5, "patience": 0.5, "curiosity": 0.5}
    sliders.update(slider_overrides)
    return Persona(
        id="test",
        name="Test Persona",
        description="A persona for testing.",
        demographic_anchors=DemographicAnchors(
            tech_affinity="medium", primary_device="mobile", discovery_mode="search-driven"
        ),
        contextual_triggers=["time constraint"],
        sliders=BehavioralSliders(**sliders),
    )


def test_slider_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_persona(anxiety=1.5)


def test_slider_negative_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_persona(patience=-0.1)


def test_system_prompt_includes_key_persona_attributes() -> None:
    persona = _make_persona(anxiety=0.85, patience=0.3)
    prompt = persona.system_prompt()
    assert "Test Persona" in prompt
    assert "mobile" in prompt
    assert "search-driven" in prompt
    assert "time constraint" in prompt
    assert "anxiety=0.85" in prompt
    assert "patience=0.3" in prompt


def test_system_prompt_handles_no_contextual_triggers() -> None:
    persona = _make_persona()
    persona = persona.model_copy(update={"contextual_triggers": []})
    assert "triggers active right now: none" in persona.system_prompt()


def test_friction_issue_requires_all_fields() -> None:
    with pytest.raises(ValidationError):
        FrictionIssue(screen="cart", severity=Severity.HIGH, title="Missing fields")  # type: ignore[call-arg]
