from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.db.models.settings import UserSettings


async def get_settings(session: AsyncSession, user_id: int) -> UserSettings:
    result = await session.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = UserSettings(user_id=user_id)
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def update_settings(session: AsyncSession, user_id: int, **kwargs) -> UserSettings:
    settings = await get_settings(session, user_id)
    for key, value in kwargs.items():
        setattr(settings, key, value)
    await session.commit()
    await session.refresh(settings)
    return settings
