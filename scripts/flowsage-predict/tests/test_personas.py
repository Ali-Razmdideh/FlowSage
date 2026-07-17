from pathlib import Path

import pytest

from flowsage_predict.personas import (
    find_baseline_persona,
    load_baseline_personas,
    load_persona,
    load_personas_from_dir,
)


def test_load_baseline_personas_returns_the_five_readme_personas() -> None:
    personas = load_baseline_personas()
    ids = {p.id for p in personas}
    assert ids == {
        "novice",
        "power_user",
        "accessibility_constrained",
        "low_patience_mobile",
        "non_native_speaker",
    }
    assert all(p.baseline for p in personas)
    assert personas == sorted(personas, key=lambda p: p.id)


def test_find_baseline_persona_returns_matching_persona() -> None:
    persona = find_baseline_persona("novice")
    assert persona.name == "Novice User"
    assert 0.0 <= persona.sliders.anxiety <= 1.0


def test_find_baseline_persona_raises_for_unknown_id() -> None:
    with pytest.raises(KeyError):
        find_baseline_persona("does-not-exist")


def test_load_persona_from_custom_yaml(tmp_path: Path) -> None:
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        """
        id: custom
        name: Custom Persona
        description: A custom test persona.
        demographic_anchors:
          tech_affinity: medium
          primary_device: desktop
          discovery_mode: search-driven
        sliders:
          technical_literacy: 0.5
          anxiety: 0.5
          patience: 0.5
          curiosity: 0.5
        """,
        encoding="utf-8",
    )
    persona = load_persona(custom)
    assert persona.id == "custom"
    assert persona.baseline is False


def test_load_persona_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_persona(bad)


def test_load_personas_from_dir_is_sorted_by_id(tmp_path: Path) -> None:
    (tmp_path / "z.yaml").write_text(
        "id: z_persona\nname: Z\ndescription: d\n"
        "demographic_anchors: {tech_affinity: low, primary_device: mobile, "
        "discovery_mode: search-driven}\n"
        "sliders: {technical_literacy: 0.1, anxiety: 0.1, patience: 0.1, curiosity: 0.1}\n",
        encoding="utf-8",
    )
    (tmp_path / "a.yaml").write_text(
        "id: a_persona\nname: A\ndescription: d\n"
        "demographic_anchors: {tech_affinity: low, primary_device: mobile, "
        "discovery_mode: search-driven}\n"
        "sliders: {technical_literacy: 0.1, anxiety: 0.1, patience: 0.1, curiosity: 0.1}\n",
        encoding="utf-8",
    )
    personas = load_personas_from_dir(tmp_path)
    assert [p.id for p in personas] == ["a_persona", "z_persona"]
