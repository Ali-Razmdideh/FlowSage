"""`/settings/integrations`: Slack/Jira connect-disconnect, API key issue/revoke,
webhook CRUD + delivery log + test-send. See the Phase 3 chunk 2 design spec."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_membership, get_db_session, require_role
from flowsage_backend.integrations.webhooks import deliver_webhook
from flowsage_backend.integrations_store import get_jira_integration, get_slack_integration
from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.models.integration import JiraIntegration, SlackIntegration
from flowsage_backend.models.user import User
from flowsage_backend.models.webhook import Webhook, WebhookDelivery
from flowsage_backend.models.workspace import Membership, Role
from flowsage_backend.security import generate_api_key, hash_api_key
from flowsage_backend.url_safety import validate_outbound_url
from flowsage_backend.webhooks_store import list_deliveries, record_delivery

router = APIRouter(prefix="/settings/integrations", tags=["integrations"])


def _mask(value: str, keep: int = 4) -> str:
    return f"...{value[-keep:]}" if len(value) > keep else "..."


class SlackStatusOut(BaseModel):
    connected: bool
    webhook_url_preview: str | None


class SlackConnectIn(BaseModel):
    webhook_url: str = Field(min_length=1, max_length=500)

    _validate_webhook_url = field_validator("webhook_url")(validate_outbound_url)


class JiraStatusOut(BaseModel):
    connected: bool
    base_url: str | None
    email: str | None
    project_key: str | None


class JiraConnectIn(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    email: str = Field(min_length=1, max_length=320)
    api_token: str = Field(min_length=1, max_length=500)
    project_key: str = Field(min_length=1, max_length=64)

    _validate_base_url = field_validator("base_url")(validate_outbound_url)


class ApiKeyListOut(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool


class ApiKeyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ApiKeyCreateOut(BaseModel):
    id: uuid.UUID
    name: str
    key: str
    key_prefix: str
    created_at: datetime


class WebhookOut(BaseModel):
    id: uuid.UUID
    url: str
    event_types: list[str]
    enabled: bool
    created_at: datetime


class WebhookCreateIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    event_types: list[str] = Field(min_length=1)

    _validate_url = field_validator("url")(validate_outbound_url)


class WebhookCreateOut(WebhookOut):
    secret: str


class WebhookUpdateIn(BaseModel):
    url: str | None = None
    event_types: list[str] | None = None
    enabled: bool | None = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        return validate_outbound_url(value) if value is not None else None


class WebhookDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    status_code: int | None
    success: bool
    created_at: datetime


class WebhookTestOut(BaseModel):
    status_code: int | None
    success: bool


@router.get("/slack", response_model=SlackStatusOut)
async def get_slack_status(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> SlackStatusOut:
    _, membership = membership_pair
    integration = await get_slack_integration(session, membership.workspace_id)
    if integration is None:
        return SlackStatusOut(connected=False, webhook_url_preview=None)
    return SlackStatusOut(connected=True, webhook_url_preview=_mask(integration.webhook_url))


@router.put("/slack", response_model=SlackStatusOut)
async def connect_slack(
    payload: SlackConnectIn,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> SlackStatusOut:
    _, membership = membership_pair
    existing = await get_slack_integration(session, membership.workspace_id)
    if existing is not None:
        existing.webhook_url = payload.webhook_url
    else:
        session.add(
            SlackIntegration(workspace_id=membership.workspace_id, webhook_url=payload.webhook_url)
        )
    await session.commit()
    return SlackStatusOut(connected=True, webhook_url_preview=_mask(payload.webhook_url))


@router.delete("/slack", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def disconnect_slack(
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _, membership = membership_pair
    integration = await get_slack_integration(session, membership.workspace_id)
    if integration is not None:
        await session.delete(integration)
        await session.commit()


@router.get("/jira", response_model=JiraStatusOut)
async def get_jira_status(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> JiraStatusOut:
    _, membership = membership_pair
    integration = await get_jira_integration(session, membership.workspace_id)
    if integration is None:
        return JiraStatusOut(connected=False, base_url=None, email=None, project_key=None)
    return JiraStatusOut(
        connected=True,
        base_url=integration.base_url,
        email=integration.email,
        project_key=integration.project_key,
    )


@router.put("/jira", response_model=JiraStatusOut)
async def connect_jira(
    payload: JiraConnectIn,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> JiraStatusOut:
    _, membership = membership_pair
    existing = await get_jira_integration(session, membership.workspace_id)
    if existing is not None:
        existing.base_url = payload.base_url
        existing.email = payload.email
        existing.api_token = payload.api_token
        existing.project_key = payload.project_key
    else:
        session.add(
            JiraIntegration(
                workspace_id=membership.workspace_id,
                base_url=payload.base_url,
                email=payload.email,
                api_token=payload.api_token,
                project_key=payload.project_key,
            )
        )
    await session.commit()
    return JiraStatusOut(
        connected=True, base_url=payload.base_url, email=payload.email, project_key=payload.project_key
    )


@router.delete("/jira", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def disconnect_jira(
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _, membership = membership_pair
    integration = await get_jira_integration(session, membership.workspace_id)
    if integration is not None:
        await session.delete(integration)
        await session.commit()


@router.get("/api-keys", response_model=list[ApiKeyListOut])
async def list_api_keys(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[ApiKeyListOut]:
    _, membership = membership_pair
    result = await session.execute(
        select(ApiKey)
        .where(ApiKey.workspace_id == membership.workspace_id)
        .order_by(ApiKey.created_at.desc())
    )
    return [
        ApiKeyListOut(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
            revoked=k.revoked_at is not None,
        )
        for k in result.scalars().all()
    ]


@router.post("/api-keys", response_model=ApiKeyCreateOut, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreateIn,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> ApiKeyCreateOut:
    _, membership = membership_pair
    raw_key = generate_api_key()
    key = ApiKey(
        workspace_id=membership.workspace_id,
        name=payload.name,
        key_prefix=raw_key[:12],
        key_hash=hash_api_key(raw_key),
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return ApiKeyCreateOut(
        id=key.id, name=key.name, key=raw_key, key_prefix=key.key_prefix, created_at=key.created_at
    )


async def _get_owned_api_key(session: AsyncSession, workspace_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey:
    key = await session.get(ApiKey, key_id)
    if key is None or key.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    return key


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def revoke_api_key(
    key_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _, membership = membership_pair
    key = await _get_owned_api_key(session, membership.workspace_id, key_id)
    key.revoked_at = datetime.now(tz=key.created_at.tzinfo)
    await session.commit()


@router.get("/webhooks", response_model=list[WebhookOut])
async def list_webhooks(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[WebhookOut]:
    _, membership = membership_pair
    result = await session.execute(
        select(Webhook)
        .where(Webhook.workspace_id == membership.workspace_id)
        .order_by(Webhook.created_at.desc())
    )
    return [
        WebhookOut(id=w.id, url=w.url, event_types=w.event_types, enabled=w.enabled, created_at=w.created_at)
        for w in result.scalars().all()
    ]


@router.post("/webhooks", response_model=WebhookCreateOut, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookCreateIn,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> WebhookCreateOut:
    _, membership = membership_pair
    secret = generate_api_key()  # same high-entropy generator; format doesn't matter here
    webhook = Webhook(
        workspace_id=membership.workspace_id,
        url=payload.url,
        secret=secret,
        event_types=payload.event_types,
    )
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)
    return WebhookCreateOut(
        id=webhook.id,
        url=webhook.url,
        event_types=webhook.event_types,
        enabled=webhook.enabled,
        created_at=webhook.created_at,
        secret=secret,
    )


async def _get_owned_webhook(session: AsyncSession, workspace_id: uuid.UUID, webhook_id: uuid.UUID) -> Webhook:
    webhook = await session.get(Webhook, webhook_id)
    if webhook is None or webhook.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook not found")
    return webhook


@router.patch("/webhooks/{webhook_id}", response_model=WebhookOut)
async def update_webhook(
    webhook_id: uuid.UUID,
    payload: WebhookUpdateIn,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> WebhookOut:
    _, membership = membership_pair
    webhook = await _get_owned_webhook(session, membership.workspace_id, webhook_id)
    if payload.url is not None:
        webhook.url = payload.url
    if payload.event_types is not None:
        webhook.event_types = payload.event_types
    if payload.enabled is not None:
        webhook.enabled = payload.enabled
    await session.commit()
    return WebhookOut(
        id=webhook.id,
        url=webhook.url,
        event_types=webhook.event_types,
        enabled=webhook.enabled,
        created_at=webhook.created_at,
    )


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_webhook(
    webhook_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _, membership = membership_pair
    webhook = await _get_owned_webhook(session, membership.workspace_id, webhook_id)
    await session.delete(webhook)
    await session.commit()


@router.get("/webhooks/{webhook_id}/deliveries", response_model=list[WebhookDeliveryOut])
async def get_webhook_deliveries(
    webhook_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[WebhookDelivery]:
    _, membership = membership_pair
    await _get_owned_webhook(session, membership.workspace_id, webhook_id)
    return await list_deliveries(session, webhook_id)


@router.post("/webhooks/{webhook_id}/test", response_model=WebhookTestOut)
async def test_webhook(
    webhook_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> WebhookTestOut:
    _, membership = membership_pair
    webhook = await _get_owned_webhook(session, membership.workspace_id, webhook_id)
    status_code, success = await deliver_webhook(
        webhook.url, secret=webhook.secret, event_type="test", payload={"message": "FlowSage test delivery"}
    )
    await record_delivery(
        session, webhook.id, "test", {"message": "FlowSage test delivery"}, status_code, success
    )
    return WebhookTestOut(status_code=status_code, success=success)
