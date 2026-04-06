from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.db.models.user import User
from bot.db.models.settings import UserSettings


async def get_or_create_user(session: AsyncSession, tg_id: int, username: str | None, first_name: str | None) -> User:
    result = await session.execute(select(User).where(User.id == tg_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(id=tg_id, username=username, first_name=first_name)
        session.add(user)
        await session.flush()  # write user row before FK reference
        settings = UserSettings(user_id=tg_id)
        session.add(settings)
        await session.commit()
        await session.refresh(user)
    return user


async def get_all_active_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).where(User.is_active == True))
    return result.scalars().all()


async def get_all_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.joined_at.desc()))
    return result.scalars().all()


async def set_premium(session: AsyncSession, user_id: int, value: bool) -> bool:
    """Returns True if user found and updated."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return False
    user.is_premium = value
    await session.commit()
    return True


async def is_premium_or_owner(session: AsyncSession, user_id: int, owner_id: int) -> bool:
    if user_id == owner_id:
        return True
    result = await session.execute(select(User.is_premium).where(User.id == user_id))
    row = result.scalar_one_or_none()
    return bool(row)
