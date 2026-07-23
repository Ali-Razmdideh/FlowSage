"""`flowsage-backend` console script: serves the API, or manages seed data."""

from __future__ import annotations

import argparse
import asyncio

import uvicorn
from sqlalchemy import select

from flowsage_backend.config import get_settings
from flowsage_backend.db import create_engine, create_session_factory
from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.models.workspace import Workspace
from flowsage_backend.security import generate_api_key, hash_api_key
from flowsage_backend.seed import seed_baseline_personas, upsert_user


async def _create_user(email: str, password: str) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        user = await upsert_user(session, email, password)
    await engine.dispose()
    print(f"User ready: {user.email} ({user.id})")


async def _seed_personas() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        result = await session.execute(select(Workspace).order_by(Workspace.created_at).limit(1))
        workspace = result.scalar_one_or_none()
        if workspace is None:
            raise SystemExit("No workspace exists yet -- run `create-user` first.")
        personas = await seed_baseline_personas(session, workspace.id)
    await engine.dispose()
    print(f"{len(personas)} baseline persona(s) ready: {', '.join(p.slug for p in personas)}")


async def _create_api_key(workspace_slug: str, name: str) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        result = await session.execute(select(Workspace).where(Workspace.slug == workspace_slug))
        workspace = result.scalar_one_or_none()
        if workspace is None:
            raise SystemExit(f"No workspace with slug {workspace_slug!r}.")
        raw_key = generate_api_key()
        session.add(
            ApiKey(
                workspace_id=workspace.id,
                name=name,
                key_prefix=raw_key[:12],
                key_hash=hash_api_key(raw_key),
            )
        )
        await session.commit()
    await engine.dispose()
    print(f"API key created for workspace {workspace_slug!r}: {raw_key}")
    print("Store it now -- it will not be shown again.")


def _serve() -> None:
    uvicorn.run("flowsage_backend.main:create_app", factory=True, host="0.0.0.0", port=8000)


def main() -> None:
    parser = argparse.ArgumentParser(prog="flowsage-backend")
    subparsers = parser.add_subparsers(dest="command", required=False)

    subparsers.add_parser("serve", help="Run the API server (default)")

    create_user_parser = subparsers.add_parser(
        "create-user", help="Create the single-tenant user, or reset its password"
    )
    create_user_parser.add_argument("email")
    create_user_parser.add_argument("password")

    subparsers.add_parser("seed-personas", help="Load the 5 baseline personas into the database")

    create_api_key_parser = subparsers.add_parser(
        "create-api-key", help="Create a POST /v1/events API key for a workspace"
    )
    create_api_key_parser.add_argument("workspace_slug")
    create_api_key_parser.add_argument("name")

    args = parser.parse_args()

    if args.command == "create-user":
        asyncio.run(_create_user(args.email, args.password))
        return

    if args.command == "seed-personas":
        asyncio.run(_seed_personas())
        return

    if args.command == "create-api-key":
        asyncio.run(_create_api_key(args.workspace_slug, args.name))
        return

    _serve()


if __name__ == "__main__":
    main()
