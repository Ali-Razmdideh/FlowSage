"""`flowsage-backend` console script: serves the API, or manages seed data."""

from __future__ import annotations

import argparse
import asyncio

import uvicorn
from sqlalchemy import select

from flowsage_backend.config import get_settings
from flowsage_backend.db import create_engine, create_session_factory
from flowsage_backend.models.workspace import Workspace
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

    args = parser.parse_args()

    if args.command == "create-user":
        asyncio.run(_create_user(args.email, args.password))
        return

    if args.command == "seed-personas":
        asyncio.run(_seed_personas())
        return

    _serve()


if __name__ == "__main__":
    main()
