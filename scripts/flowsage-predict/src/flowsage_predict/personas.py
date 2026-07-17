"""Loading and validating persona definitions from YAML files."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml

from flowsage_predict.models import Persona

BASELINE_PERSONAS_PACKAGE = "flowsage_predict.baseline_personas"


def load_persona(path: Path) -> Persona:
    """Parse a single persona YAML file into a validated `Persona`."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Persona file {path} must contain a YAML mapping at the top level")
    return Persona.model_validate(raw)


def load_personas_from_dir(directory: Path) -> list[Persona]:
    """Load every `*.yaml` persona definition in a directory, sorted by id."""
    personas = [load_persona(p) for p in sorted(directory.glob("*.yaml"))]
    return sorted(personas, key=lambda persona: persona.id)


def load_baseline_personas() -> list[Persona]:
    """Load the 5 baseline personas shipped with this package (README §Features)."""
    with resources.as_file(resources.files(BASELINE_PERSONAS_PACKAGE)) as personas_dir:
        return load_personas_from_dir(personas_dir)


def find_baseline_persona(persona_id: str) -> Persona:
    """Look up one baseline persona by id, raising `KeyError` if it doesn't exist."""
    for persona in load_baseline_personas():
        if persona.id == persona_id:
            return persona
    raise KeyError(f"No baseline persona named {persona_id!r}")
