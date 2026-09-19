"""Inspect the actual Compose merge without starting services or exposing real secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMMAND = [
    "docker",
    "compose",
    "-f",
    "infra/docker-compose.yml",
    "-f",
    "infra/docker-compose.prod.yml",
    "config",
    "--format",
    "json",
]


def render(overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is unavailable")
    return subprocess.run(
        COMMAND,
        cwd=ROOT,
        env={**os.environ, "JWT_SECRET": "j" * 32, "SECRET_ENCRYPTION_KEY": "e" * 32, **overrides},
        capture_output=True,
        text=True,
    )


def test_production_forces_secure_environment_for_backend_and_worker() -> None:
    result = render({"ENVIRONMENT": "development", "COOKIE_SECURE": "false"})
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    for name in ("backend", "worker"):
        environment = services[name]["environment"]
        assert environment["ENVIRONMENT"] == "production"
        assert environment["COOKIE_SECURE"] == "true"
        assert environment["JWT_SECRET"] == "j" * 32
        assert environment["SECRET_ENCRYPTION_KEY"] == "e" * 32


@pytest.mark.parametrize("name", ["JWT_SECRET", "SECRET_ENCRYPTION_KEY"])
def test_production_requires_explicit_secret(name: str) -> None:
    result = render({name: ""})
    assert result.returncode != 0
    assert name in result.stderr
