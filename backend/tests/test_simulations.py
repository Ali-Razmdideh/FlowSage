import uuid
from pathlib import Path

import pytest
from flowsage_predict.models import FrictionIssue as PredictFrictionIssue
from flowsage_predict.models import Persona as PredictPersona
from flowsage_predict.models import ScreenEvaluation, Severity
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.persona import Persona
from flowsage_backend.models.simulation import RunStatus
from flowsage_backend.models.workspace import Workspace
from flowsage_backend.seed import seed_baseline_personas
from flowsage_backend.simulations import SimulationError, create_run, execute_simulation


async def _create_workspace(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace.id


class ScriptedVisionClient:
    """Fake VisionClient (matches flowsage_predict.vision.VisionClient's protocol)."""

    def __init__(self, evaluations: list[ScreenEvaluation]) -> None:
        self._evaluations = evaluations
        self.calls = 0

    def evaluate_screen(
        self,
        persona: PredictPersona,
        goal: str,
        screenshot: Path,
        history: list[object],
    ) -> ScreenEvaluation:
        evaluation = self._evaluations[self.calls]
        self.calls += 1
        return evaluation


class FailingVisionClient:
    def evaluate_screen(self, *args: object, **kwargs: object) -> ScreenEvaluation:
        raise RuntimeError("Claude API is unreachable")


async def _seed_persona(db_session: AsyncSession, workspace_id: uuid.UUID) -> Persona:
    personas = await seed_baseline_personas(db_session, workspace_id)
    return next(p for p in personas if p.slug == "novice")


async def test_create_run_requires_existing_persona(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    (tmp_path / "01.png").write_bytes(b"fake")
    with pytest.raises(SimulationError, match="No persona"):
        await create_run(
            db_session,
            workspace_id=workspace_id,
            persona_id=uuid.uuid4(),
            flow_name="Checkout",
            goal="Complete purchase",
            screenshots_dir=tmp_path,
        )


async def test_create_run_requires_screenshots(db_session: AsyncSession, tmp_path: Path) -> None:
    workspace_id = await _create_workspace(db_session)
    persona = await _seed_persona(db_session, workspace_id)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(SimulationError, match="No screenshots"):
        await create_run(
            db_session,
            workspace_id=workspace_id,
            persona_id=persona.id,
            flow_name="Checkout",
            goal="Complete purchase",
            screenshots_dir=empty_dir,
        )


async def test_create_run_succeeds(db_session: AsyncSession, tmp_path: Path) -> None:
    workspace_id = await _create_workspace(db_session)
    persona = await _seed_persona(db_session, workspace_id)
    (tmp_path / "01_cart.png").write_bytes(b"fake")

    run = await create_run(
        db_session,
        workspace_id=workspace_id,
        persona_id=persona.id,
        flow_name="Checkout",
        goal="Complete purchase",
        screenshots_dir=tmp_path,
    )

    assert run.status == RunStatus.QUEUED
    assert run.persona_id == persona.id


async def test_execute_simulation_persists_steps_and_completes(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    persona = await _seed_persona(db_session, workspace_id)
    (tmp_path / "01_cart.png").write_bytes(b"fake")
    (tmp_path / "02_shipping.png").write_bytes(b"fake")
    run = await create_run(
        db_session,
        workspace_id=workspace_id,
        persona_id=persona.id,
        flow_name="Checkout",
        goal="Complete purchase",
        screenshots_dir=tmp_path,
    )

    friction = PredictFrictionIssue(
        screen="02_shipping",
        severity=Severity.HIGH,
        title="Zip code validation rejects valid input",
        heuristic_violated="Error Prevention",
        persona_impact="Blocks completion",
        description="Regex rejects zip+4",
        suggested_fix="Accept zip+4",
    )
    client = ScriptedVisionClient(
        [
            ScreenEvaluation(action="Adds item to cart", reasoning="Wants to buy it"),
            ScreenEvaluation(
                action="Struggles with the zip field",
                reasoning="Format is unclear",
                friction=friction,
            ),
        ]
    )

    await execute_simulation(db_session, run.id, client)

    await db_session.refresh(run, attribute_names=["steps", "issues", "status"])
    assert run.status == RunStatus.COMPLETED
    assert run.started_at is not None
    assert run.finished_at is not None
    assert [s.screen for s in run.steps] == ["01_cart", "02_shipping"]
    assert [s.sequence for s in run.steps] == [0, 1]
    assert len(run.issues) == 1
    assert run.issues[0].severity == "high"
    assert run.issues[0].step_id == run.steps[1].id


async def test_execute_simulation_marks_run_failed_on_error(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    persona = await _seed_persona(db_session, workspace_id)
    (tmp_path / "01_cart.png").write_bytes(b"fake")
    run = await create_run(
        db_session,
        workspace_id=workspace_id,
        persona_id=persona.id,
        flow_name="Checkout",
        goal="Complete purchase",
        screenshots_dir=tmp_path,
    )

    with pytest.raises(RuntimeError, match="unreachable"):
        await execute_simulation(db_session, run.id, FailingVisionClient())

    await db_session.refresh(run, attribute_names=["status", "error"])
    assert run.status == RunStatus.FAILED
    assert run.error is not None and "unreachable" in run.error


async def test_execute_simulation_raises_for_unknown_run(db_session: AsyncSession) -> None:
    with pytest.raises(SimulationError, match="No simulation run"):
        await execute_simulation(db_session, uuid.uuid4(), ScriptedVisionClient([]))
