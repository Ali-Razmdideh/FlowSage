"""Create or update the single-tenant admin user.

Phase 1 is single-tenant with manual onboarding (README roadmap) -- there is no
public registration endpoint. The one user account is seeded via the
`flowsage-backend create-user` CLI command, which calls `upsert_user` below.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.user import User
from flowsage_backend.security import hash_password


async def upsert_user(session: AsyncSession, email: str, password: str) -> User:
    """Create the user if it doesn't exist, or reset its password if it does."""
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(email=email, hashed_password=hash_password(password))
        session.add(user)
    else:
        user.hashed_password = hash_password(password)
    await session.commit()
    await session.refresh(user)
    return user
