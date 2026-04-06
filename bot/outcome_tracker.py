"""
Outcome Tracker — runs every 15 minutes.

For every OPEN signal it:
  1. Fetches current price
  2. Checks if TP1 / TP2 / TP3 / SL was hit
  3. Updates outcome in DB
  4. Notifies users whose signal resolved

Expiry: signals open for >48h are marked EXPIRED.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from bot.db.session import AsyncSessionLocal
from bot.db.repositories.signal_repo import get_open_signals, update_outcome
from bot.db.models.signal import Signal
from src.instruments import get_ticker_for_symbol
from src.data_fetcher import get_current_price
from bot.formatter import _fmt

logger = logging.getLogger(__name__)

EXPIRY_HOURS = 48
CHECK_INTERVAL_MINUTES = 15


def _check_outcome(signal: Signal, price: float) -> str | None:
    """
    Returns new outcome if price has hit a level, else None.
    Progressively checks TP3 → TP2 → TP1 → SL (best first).
    """
    entry = float(signal.entry_price)
    sl    = float(signal.stop_loss)
    tp1   = float(signal.tp1)
    tp2   = float(signal.tp2)
    tp3   = float(signal.tp3)

    if signal.direction == "BUY":
        if price >= tp3: return "TP3"
        if price >= tp2: return "TP2"
        if price >= tp1: return "TP1"
        if price <= sl:  return "SL"
    else:  # SELL
        if price <= tp3: return "TP3"
        if price <= tp2: return "TP2"
        if price <= tp1: return "TP1"
        if price >= sl:  return "SL"

    return None


def _outcome_message(signal: Signal, outcome: str, current_price: float) -> str:
    labels = {
        "TP1": "🎯 TP1 Reached",
        "TP2": "🎯 TP2 Reached",
        "TP3": "🎯 TP3 Reached",
        "SL":  "❌ Stop Loss Hit",
        "EXPIRED": "⏸ Signal Closed",
    }
    arrow = "▲" if signal.direction == "BUY" else "▼"

    return "\n".join([
        f"{arrow} <b>{signal.symbol} {signal.direction}  —  {labels.get(outcome, outcome)}</b>",
        "",
        f"Entry    <code>{_fmt(float(signal.entry_price))}</code>",
        f"Close    <code>{_fmt(current_price)}</code>",
    ])


async def run_outcome_tracker(bot, interval_minutes: int = CHECK_INTERVAL_MINUTES):
    logger.info(f"Outcome tracker started — checking every {interval_minutes}m")

    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            async with AsyncSessionLocal() as session:
                open_signals = await get_open_signals(session)

            if not open_signals:
                continue

            logger.info(f"Checking outcomes for {len(open_signals)} open signals")

            for signal in open_signals:
                try:
                    # Check expiry
                    fired = signal.fired_at if signal.fired_at.tzinfo else signal.fired_at.replace(tzinfo=timezone.utc)
                    age = datetime.now(timezone.utc) - fired
                    if age > timedelta(hours=EXPIRY_HOURS):
                        async with AsyncSessionLocal() as session:
                            await update_outcome(session, signal.id, "EXPIRED")
                        logger.info(f"Signal {signal.id} ({signal.symbol}) expired")
                        continue

                    # Fetch current price
                    ticker = get_ticker_for_symbol(signal.symbol)
                    loop = asyncio.get_running_loop()
                    price = await loop.run_in_executor(None, lambda t=ticker: get_current_price(t))

                    outcome = _check_outcome(signal, price)
                    if outcome is None:
                        continue

                    # Update DB
                    async with AsyncSessionLocal() as session:
                        await update_outcome(session, signal.id, outcome)

                    logger.info(f"Signal {signal.id} ({signal.symbol} {signal.direction}) → {outcome} @ {price}")

                    # Notify all users who received this signal
                    async with AsyncSessionLocal() as session:
                        from sqlalchemy import select
                        from bot.db.models.signal import SignalDelivery
                        result = await session.execute(
                            select(SignalDelivery.user_id)
                            .where(SignalDelivery.signal_id == signal.id)
                        )
                        user_ids = [row[0] for row in result.all()]

                    for user_id in user_ids:
                        try:
                            msg = _outcome_message(signal, outcome, price)
                            await bot.send_message(user_id, msg, parse_mode="HTML")
                        except Exception as e:
                            logger.error(f"Failed to notify {user_id}: {e}")
                        await asyncio.sleep(0.1)

                except Exception as e:
                    logger.error(f"Error checking signal {signal.id}: {e}")

        except Exception as e:
            logger.error(f"Outcome tracker error: {e}", exc_info=True)
