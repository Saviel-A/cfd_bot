from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from bot.db.models.watchlist import UserWatchlist


async def get_watchlist(session: AsyncSession, user_id: int) -> list[str]:
    result = await session.execute(
        select(UserWatchlist.symbol).where(UserWatchlist.user_id == user_id)
    )
    return [row[0] for row in result.all()]


async def add_symbol(session: AsyncSession, user_id: int, symbol: str) -> bool:
    """Returns True if added, False if already exists."""
    existing = await session.execute(
        select(UserWatchlist).where(
            UserWatchlist.user_id == user_id,
            UserWatchlist.symbol == symbol.upper(),
        )
    )
    if existing.scalar_one_or_none():
        return False
    session.add(UserWatchlist(user_id=user_id, symbol=symbol.upper()))
    await session.commit()
    return True


async def remove_symbol(session: AsyncSession, user_id: int, symbol: str) -> bool:
    """Returns True if removed, False if not found."""
    result = await session.execute(
        delete(UserWatchlist).where(
            UserWatchlist.user_id == user_id,
            UserWatchlist.symbol == symbol.upper(),
        ).returning(UserWatchlist.id)
    )
    await session.commit()
    return len(result.all()) > 0
