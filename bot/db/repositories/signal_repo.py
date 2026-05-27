from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from bot.db.models.signal import Signal
from datetime import datetime, timezone

NON_TRADE_OUTCOMES = ("SUPERSEDED", "SKIPPED", "REPLACED", "EXPIRED", "TP1", "TP2", "TP1_OPEN", "TP2_OPEN")
ACTIVE_OUTCOMES = ("OPEN",)
FINAL_OUTCOMES = ("TP", "TP3", "SL")


async def save_signal(session: AsyncSession, data: dict) -> Signal:
    # Keep stats clean: a newer alert removes the previous unclosed alert
    # instead of creating a visible "replaced" result.
    await session.execute(
        delete(Signal)
        .where(Signal.symbol == data["symbol"], Signal.outcome.in_(ACTIVE_OUTCOMES))
    )
    signal = Signal(**data)
    session.add(signal)
    await session.commit()
    await session.refresh(signal)
    return signal


async def get_last_signal_for_symbol(session: AsyncSession, symbol: str) -> Signal | None:
    result = await session.execute(
        select(Signal)
        .where(Signal.symbol == symbol, Signal.outcome.not_in(NON_TRADE_OUTCOMES))
        .order_by(Signal.fired_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_open_signals(session: AsyncSession) -> list[Signal]:
    result = await session.execute(
        select(Signal).where(Signal.outcome.in_(ACTIVE_OUTCOMES))
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
        .where(Signal.outcome.not_in(NON_TRADE_OUTCOMES))
        .order_by(Signal.fired_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def get_signal_stats(session: AsyncSession, limit: int = 100) -> dict:
    result = await session.execute(
        select(Signal)
        .where(Signal.outcome.not_in(NON_TRADE_OUTCOMES))
        .order_by(Signal.fired_at.desc())
        .limit(limit)
    )
    signals = result.scalars().all()
    closed = [s for s in signals if s.outcome in FINAL_OUTCOMES]
    wins = [s for s in closed if s.outcome in ("TP", "TP3")]
    losses = [s for s in closed if s.outcome == "SL"]

    by_symbol: dict[str, dict] = {}
    for s in closed:
        bucket = by_symbol.setdefault(
            s.symbol,
            {
                "symbol": s.symbol,
                "wins": 0,
                "losses": 0,
                "tp": 0,
                "total": 0,
            },
        )
        bucket["total"] += 1
        if s.outcome in ("TP", "TP3"):
            bucket["wins"] += 1
            bucket["tp"] += 1
        elif s.outcome == "SL":
            bucket["losses"] += 1

    ranked = sorted(
        by_symbol.values(),
        key=lambda item: (item["wins"] - item["losses"], item["wins"], item["total"]),
        reverse=True,
    )

    return {
        "limit": limit,
        "total": len(closed),
        "open": sum(1 for s in signals if s.outcome in ACTIVE_OUTCOMES),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "tp": len(wins),
        "recent_closed": closed[:8],
        "best_symbols": ranked[:3],
        "worst_symbols": list(reversed(ranked[-3:])) if ranked else [],
    }


async def clear_all_signals(session: AsyncSession) -> int:
    result = await session.execute(delete(Signal))
    await session.commit()
    return result.rowcount


async def delete_non_trade_signals(session: AsyncSession) -> int:
    result = await session.execute(
        delete(Signal).where(Signal.outcome.in_(NON_TRADE_OUTCOMES))
    )
    await session.commit()
    return result.rowcount
