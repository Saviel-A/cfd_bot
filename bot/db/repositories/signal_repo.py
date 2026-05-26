from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from bot.db.models.signal import Signal
from datetime import datetime, timezone


async def save_signal(session: AsyncSession, data: dict) -> Signal:
    # Close any existing open signal for this symbol before creating a new one
    await session.execute(
        update(Signal)
        .where(Signal.symbol == data["symbol"], Signal.outcome == "OPEN")
        .values(outcome="SUPERSEDED", outcome_at=datetime.now(timezone.utc))
    )
    signal = Signal(**data)
    session.add(signal)
    await session.commit()
    await session.refresh(signal)
    return signal


async def get_last_signal_for_symbol(session: AsyncSession, symbol: str) -> Signal | None:
    result = await session.execute(
        select(Signal)
        .where(Signal.symbol == symbol)
        .order_by(Signal.fired_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_open_signals(session: AsyncSession) -> list[Signal]:
    result = await session.execute(
        select(Signal).where(Signal.outcome == "OPEN")
    )
    return result.scalars().all()


async def update_outcome(session: AsyncSession, signal_id: int, outcome: str):
    await session.execute(
        update(Signal)
        .where(Signal.id == signal_id)
        .values(outcome=outcome, outcome_at=datetime.now(timezone.utc))
    )
    await session.commit()


async def get_recent_signals(session: AsyncSession, limit: int = 20) -> list[Signal]:
    result = await session.execute(
        select(Signal)
        .order_by(Signal.fired_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def get_signal_stats(session: AsyncSession, limit: int = 100) -> dict:
    result = await session.execute(
        select(Signal)
        .order_by(Signal.fired_at.desc())
        .limit(limit)
    )
    signals = result.scalars().all()
    closed = [s for s in signals if s.outcome not in ("OPEN", "SUPERSEDED")]
    wins = [s for s in closed if s.outcome == "TP3"]
    partials = [s for s in closed if s.outcome in ("TP1", "TP2")]
    losses = [s for s in closed if s.outcome == "SL"]
    expired = [s for s in closed if s.outcome == "EXPIRED"]

    by_symbol: dict[str, dict] = {}
    for s in closed:
        bucket = by_symbol.setdefault(
            s.symbol,
            {
                "symbol": s.symbol,
                "wins": 0,
                "partials": 0,
                "losses": 0,
                "expired": 0,
                "tp1": 0,
                "tp2": 0,
                "tp3": 0,
                "total": 0,
            },
        )
        bucket["total"] += 1
        if s.outcome == "TP1":
            bucket["partials"] += 1
            bucket["tp1"] += 1
        elif s.outcome == "TP2":
            bucket["partials"] += 1
            bucket["tp2"] += 1
        elif s.outcome == "TP3":
            bucket["wins"] += 1
            bucket["tp3"] += 1
        elif s.outcome == "SL":
            bucket["losses"] += 1
        elif s.outcome == "EXPIRED":
            bucket["expired"] += 1

    ranked = sorted(
        by_symbol.values(),
        key=lambda item: (item["wins"] - item["losses"], item["wins"], item["total"]),
        reverse=True,
    )

    return {
        "limit": limit,
        "total": len(signals),
        "open": sum(1 for s in signals if s.outcome == "OPEN"),
        "closed": len(closed),
        "wins": len(wins),
        "partials": len(partials),
        "losses": len(losses),
        "expired": len(expired),
        "tp1": sum(1 for s in closed if s.outcome == "TP1"),
        "tp2": sum(1 for s in closed if s.outcome == "TP2"),
        "tp3": sum(1 for s in closed if s.outcome == "TP3"),
        "recent_closed": closed[:8],
        "best_symbols": ranked[:3],
        "worst_symbols": list(reversed(ranked[-3:])) if ranked else [],
    }


async def clear_all_signals(session: AsyncSession) -> int:
    result = await session.execute(delete(Signal))
    await session.commit()
    return result.rowcount
