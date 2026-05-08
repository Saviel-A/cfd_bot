"""
Outcome Tracker: runs every 15 minutes.
Checks all OPEN signals, detects if TP1/TP2/TP3/SL was hit,
updates the DB, and posts the result to the broadcast channel.
Signals open for more than 48 hours are marked EXPIRED.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from bot.config import cfg
from bot.db.session import AsyncSessionLocal
from bot.db.repositories.signal_repo import get_open_signals, update_outcome
from bot.db.models.signal import Signal
from src.data_fetcher import get_live_price
from bot.formatter import _fmt_alert as _fmt

logger = logging.getLogger(__name__)

EXPIRY_HOURS        = 48
CHECK_INTERVAL_MINUTES = 15


def _check_outcome(signal: Signal, price: float) -> str | None:
    sl  = float(signal.stop_loss)
    tp1 = float(signal.tp1)
    tp2 = float(signal.tp2)
    tp3 = float(signal.tp3)

    if signal.direction == "BUY":
        if price >= tp3: return "TP3"
        if price >= tp2: return "TP2"
        if price >= tp1: return "TP1"
        if price <= sl:  return "SL"
    else:
        if price <= tp3: return "TP3"
        if price <= tp2: return "TP2"
        if price <= tp1: return "TP1"
        if price >= sl:  return "SL"
    return None


def _outcome_message(signal: Signal, outcome: str, price: float) -> str:
    dot   = "📈" if signal.direction == "BUY" else "📉"
    label = "BUY" if signal.direction == "BUY" else "SELL"
    entry = _fmt(float(signal.entry_price))
    sl    = _fmt(float(signal.stop_loss))
    tp1   = _fmt(float(signal.tp1))
    tp2   = _fmt(float(signal.tp2))
    tp3   = _fmt(float(signal.tp3))
    now   = _fmt(price)

    title = {
        "TP1": "🎯 TP1 hit",
        "TP2": "🎯 TP2 hit",
        "TP3": "🏆 TP3 hit",
        "SL": "❌ Stop Loss hit",
        "EXPIRED": "⏸ Signal expired",
    }.get(outcome, outcome)

    action = {
        "TP1": f"Secure partial profit. Consider moving SL to entry {entry}.",
        "TP2": f"Consider locking more profit. Next target: {tp3}.",
        "TP3": "Final target reached.",
        "SL": "Trade is closed. Do not hold after SL.",
        "EXPIRED": "Close if still open.",
    }.get(outcome, "")

    return (
        f"{dot} <b>{signal.symbol} {label}</b>\n\n"
        f"<b>{title}</b>\n"
        f"Entry: <code>{entry}</code>\n"
        f"Now: <code>{now}</code>\n"
        f"SL: <code>{sl}</code> | TP: <code>{tp1}</code> / <code>{tp2}</code> / <code>{tp3}</code>\n\n"
        f"{action}"
    )


async def run_outcome_tracker(bot, interval_minutes: int = CHECK_INTERVAL_MINUTES):
    logger.info(f"Outcome tracker started  checking every {interval_minutes}m")

    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            async with AsyncSessionLocal() as session:
                open_signals = await get_open_signals(session)

            if not open_signals:
                continue

            logger.info(f"Checking {len(open_signals)} open signals")

            for signal in open_signals:
                try:
                    fired = signal.fired_at if signal.fired_at.tzinfo else signal.fired_at.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - fired > timedelta(hours=EXPIRY_HOURS):
                        async with AsyncSessionLocal() as session:
                            await update_outcome(session, signal.id, "EXPIRED")
                        logger.info(f"Signal {signal.id} ({signal.symbol}) expired")
                        continue

                    loop  = asyncio.get_running_loop()
                    price = await loop.run_in_executor(None, lambda s=signal.symbol: get_live_price(s))

                    outcome = _check_outcome(signal, price)
                    if outcome is None:
                        continue

                    async with AsyncSessionLocal() as session:
                        await update_outcome(session, signal.id, outcome)

                    logger.info(f"Signal {signal.id} ({signal.symbol} {signal.direction}) {outcome} @ {price}")

                    if cfg.BROADCAST_CHANNEL_ID:
                        try:
                            await bot.send_message(
                                cfg.BROADCAST_CHANNEL_ID,
                                _outcome_message(signal, outcome, price),
                                parse_mode="HTML",
                            )
                        except Exception as e:
                            logger.error(f"Outcome broadcast failed for signal {signal.id}: {e}")

                except Exception as e:
                    logger.error(f"Error checking signal {signal.id}: {e}")

        except Exception as e:
            logger.error(f"Outcome tracker error: {e}", exc_info=True)
