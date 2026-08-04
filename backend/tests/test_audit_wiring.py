from __future__ import annotations

import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models import AuditLog
from flowsage_backend.seed import seed_baseline_personas, upsert_user


async def test_login_is_audited(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"audit-login-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/auth/login", json={"email": email, "password": "hunter2"})
    assert response.status_code == 200
    workspace_id = uuid.UUID(response.json()["workspace_id"])

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.workspace_id == workspace_id, AuditLog.action == "auth.login"
        )
    )
    assert result.scalar_one_or_none() is not None


async def test_member_role_change_is_audited(app: FastAPI, db_session: AsyncSession) -> None:
    from tests.conftest import create_workspace_and_admin

    admin_user, admin_membership = await create_workspace_and_admin(
        db_session, f"audit-role-admin-{uuid.uuid4().hex[:8]}@example.com"
    )
    other_email = f"audit-role-other-{uuid.uuid4().hex[:8]}@example.com"
    other_user = await upsert_user(db_session, other_email, "hunter2")
    from flowsage_backend.models.workspace import Membership, Role

    db_session.add(
        Membership(
            user_id=other_user.id, workspace_id=admin_membership.workspace_id, role=Role.VIEWER
        )
    )
    await db_session.commit()
    result = await db_session.execute(
        select(Membership).where(
            Membership.user_id == other_user.id,
            Membership.workspace_id == admin_membership.workspace_id,
        )
    )
    other_membership_id = result.scalar_one().id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": admin_user.email, "password": "hunter2"})
        await client.post(
            "/auth/switch-workspace", json={"workspace_id": str(admin_membership.workspace_id)}
        )
        response = await client.patch(
            f"/workspaces/current/members/{other_membership_id}", json={"role": "admin"}
        )
    assert response.status_code == 200

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.workspace_id == admin_membership.workspace_id,
            AuditLog.action == "member.role_changed",
        )
    )
    entry = result.scalar_one()
    assert entry.target_id == str(other_membership_id)
    assert entry.actor_user_id == admin_user.id


async def test_scheduled_simulation_update_delete_and_push_are_audited(
    app: FastAPI, db_session: AsyncSession
) -> None:
    from tests.conftest import create_workspace_and_admin

    admin_user, admin_membership = await create_workspace_and_admin(
        db_session, f"audit-sched-{uuid.uuid4().hex[:8]}@example.com"
    )
    personas = await seed_baseline_personas(db_session, admin_membership.workspace_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": admin_user.email, "password": "hunter2"})
        await client.post(
            "/auth/switch-workspace", json={"workspace_id": str(admin_membership.workspace_id)}
        )

        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(personas[0].id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "on_push",
            },
        )
        config_id = create_response.json()["id"]

        update_response = await client.patch(
            f"/scheduled-simulations/{config_id}", json={"active": False}
        )
        assert update_response.status_code == 200

        push_response = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": ("01_cart.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        )
        assert push_response.status_code == 200

        delete_response = await client.delete(f"/scheduled-simulations/{config_id}")
        assert delete_response.status_code == 204

    result = await db_session.execute(
        select(AuditLog.action).where(
            AuditLog.workspace_id == admin_membership.workspace_id,
            AuditLog.target_id == config_id,
        )
    )
    actions = set(result.scalars().all())
    assert actions == {
        "scheduled_simulation.created",
        "scheduled_simulation.updated",
        "scheduled_simulation.screenshots_pushed",
        "scheduled_simulation.deleted",
    }
