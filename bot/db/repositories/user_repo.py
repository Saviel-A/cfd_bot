from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.db.models.user import User


async def get_or_create_user(session: AsyncSession, tg_id: int, username: str | None, first_name: str | None) -> User:
    result = await session.execute(select(User).where(User.id == tg_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(id=tg_id, username=username, first_name=first_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def get_all_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.joined_at.desc()))
    return result.scalars().all()


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    clean = username.lstrip("@").lower()
    result = await session.execute(select(User).where(User.username.ilike(clean)))
    return result.scalar_one_or_none()


async def mark_invited(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return False
    user.is_invited = True
    await session.commit()
    return True


async def delete_user(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return False
    await session.delete(user)
    await session.commit()
    return True


async def grant_premium_until(session: AsyncSession, user_id: int, until: datetime) -> bool:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return False
    user.is_premium = True
    user.premium_until = until
    await session.commit()
    return True
