from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.integrations.webhooks import deliver_webhook
from flowsage_backend.models.webhook import Webhook
from flowsage_backend.models.workspace import Workspace
from flowsage_backend.webhooks_store import list_deliveries, record_delivery


async def test_deliver_webhook_signs_body_and_returns_success() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["signature"] = request.headers["X-FlowSage-Signature"]
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    status_code, success = await deliver_webhook(
        "https://example.test/hook",
        secret="s3cr3t",
        event_type="alert.triggered",
        payload={"calibration_alerts": [], "churn_alerts": []},
        transport=transport,
    )

    assert status_code == 200
    assert success is True
    expected_signature = "sha256=" + hmac.new(
        b"s3cr3t", captured["body"], hashlib.sha256  # type: ignore[arg-type]
    ).hexdigest()
    assert captured["signature"] == expected_signature


async def test_deliver_webhook_reports_failure_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    status_code, success = await deliver_webhook(
        "https://example.test/hook",
        secret="s3cr3t",
        event_type="alert.triggered",
        payload={},
        transport=transport,
    )

    assert status_code == 500
    assert success is False


async def test_deliver_webhook_handles_connection_error_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    transport = httpx.MockTransport(handler)
    status_code, success = await deliver_webhook(
        "https://example.test/hook",
        secret="s3cr3t",
        event_type="alert.triggered",
        payload={},
        transport=transport,
    )

    assert status_code is None
    assert success is False


async def test_record_and_list_deliveries(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Webhook Store Test", slug=f"webhook-store-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)

    webhook = Webhook(
        workspace_id=workspace.id,
        url="https://example.test/hook",
        secret="s3cr3t",
        event_types=["alert.triggered"],
    )
    db_session.add(webhook)
    await db_session.commit()
    await db_session.refresh(webhook)

    await record_delivery(db_session, webhook.id, "alert.triggered", {"x": 1}, 200, True)
    await record_delivery(db_session, webhook.id, "alert.triggered", {"x": 2}, 500, False)

    deliveries = await list_deliveries(db_session, webhook.id)
    assert len(deliveries) == 2
    assert deliveries[0].success is False  # newest first
    assert json.loads(deliveries[0].payload) == {"x": 2}
