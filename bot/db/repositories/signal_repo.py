from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from bot.db.models.signal import Signal, SignalDelivery
from datetime import datetime, timezone


async def save_signal(session: AsyncSession, data: dict) -> Signal:
    signal = Signal(**data)
    session.add(signal)
    await session.commit()
    await session.refresh(signal)
    return signal


async def record_delivery(session: AsyncSession, signal_id: int, user_id: int):
    delivery = SignalDelivery(signal_id=signal_id, user_id=user_id)
    session.add(delivery)
    await session.commit()


async def get_last_signal_for_symbol(session: AsyncSession, symbol: str) -> Signal | None:
    result = await session.execute(
        select(Signal)
        .where(Signal.symbol == symbol)
        .order_by(Signal.fired_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_open_signals(session: AsyncSession) -> list[Signal]:
    """All signals still waiting for outcome."""
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


async def get_recent_signals(session: AsyncSession, user_id: int, limit: int = 10) -> list[Signal]:
    result = await session.execute(
        select(Signal)
        .join(SignalDelivery, SignalDelivery.signal_id == Signal.id)
        .where(SignalDelivery.user_id == user_id)
        .order_by(Signal.fired_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def get_performance_stats(session: AsyncSession, user_id: int) -> dict:
    """Calculate win rate and stats from resolved signals."""
    result = await session.execute(
        select(Signal)
        .join(SignalDelivery, SignalDelivery.signal_id == Signal.id)
        .where(
            SignalDelivery.user_id == user_id,
            Signal.outcome != "OPEN",
            Signal.outcome != "EXPIRED",
        )
    )
    signals = result.scalars().all()

    if not signals:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "tp1": 0, "tp2": 0, "tp3": 0, "sl": 0}

    tp1 = sum(1 for s in signals if s.outcome == "TP1")
    tp2 = sum(1 for s in signals if s.outcome == "TP2")
    tp3 = sum(1 for s in signals if s.outcome == "TP3")
    sl  = sum(1 for s in signals if s.outcome == "SL")
    wins = tp1 + tp2 + tp3
    total = wins + sl

    return {
        "total":    total,
        "wins":     wins,
        "losses":   sl,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
        "tp1":      tp1,
        "tp2":      tp2,
        "tp3":      tp3,
        "sl":       sl,
    }
