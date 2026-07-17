from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.security import verify_password
from flowsage_backend.seed import upsert_user


async def test_upsert_user_creates_new_user(db_session: AsyncSession) -> None:
    user = await upsert_user(db_session, "new-user@example.com", "hunter2")

    assert user.email == "new-user@example.com"
    assert verify_password("hunter2", user.hashed_password)


async def test_upsert_user_resets_password_for_existing_user(db_session: AsyncSession) -> None:
    first = await upsert_user(db_session, "reset-me@example.com", "old-password")
    second = await upsert_user(db_session, "reset-me@example.com", "new-password")

    assert first.id == second.id
    assert verify_password("new-password", second.hashed_password)
    assert not verify_password("old-password", second.hashed_password)
