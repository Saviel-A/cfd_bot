"""
Outcome Tracker: polls open trades frequently.
Checks all OPEN signals and detects only final TP/SL outcomes.
updates the DB, and posts the result to the broadcast channel.
"""

import asyncio
import logging

from bot.config import cfg
from bot.db.session import AsyncSessionLocal
from bot.db.repositories.signal_repo import get_open_signals, update_outcome
from bot.db.models.signal import Signal
from src.data_fetcher import get_live_quote
from bot.formatter import _fmt_alert as _fmt

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 10


def _check_outcome(signal: Signal, price: float) -> str | None:
    sl  = float(signal.stop_loss)
    tp  = float(signal.tp)

    if signal.direction == "BUY":
        if price >= tp: return "TP"
        if price <= sl:  return "SL"
    else:
        if price <= tp: return "TP"
        if price >= sl:  return "SL"
    return None


def _check_quote_outcome(signal: Signal, quote: dict[str, float]) -> tuple[str | None, float]:
    bid = float(quote["bid"])
    ask = float(quote["ask"])
    sl  = float(signal.stop_loss)
    tp  = float(signal.tp)

    # Stats track whether the market reached the alert level, not whether a
    # specific broker order filled. Use any quoted side that touches TP/SL.
    if signal.direction == "BUY":
        if max(bid, ask) >= tp:
            return "TP", max(bid, ask)
        if min(bid, ask) <= sl:
            return "SL", min(bid, ask)
    else:
        if min(bid, ask) <= tp:
            return "TP", min(bid, ask)
        if max(bid, ask) >= sl:
            return "SL", max(bid, ask)
    return None, (bid + ask) / 2


def _outcome_message(signal: Signal, outcome: str, price: float) -> str:
    dot   = "📈" if signal.direction == "BUY" else "📉"
    label = "BUY" if signal.direction == "BUY" else "SELL"
    entry = _fmt(float(signal.entry_price))
    sl    = _fmt(float(signal.stop_loss))
    tp    = _fmt(float(signal.tp))
    now   = _fmt(price)

    if outcome == "TP":
        header = "🎯 <b>TP Hit</b>"
        action = "Trade closed in profit."
    elif outcome == "SL":
        header = "🛑 <b>Stop Loss Hit</b>"
        action = "Trade closed. Wait for the next signal."
    else:
        return ""

    return (
        f"{dot} <b>{signal.symbol} {label}</b>\n\n"
        f"{header}\n\n"
        f"📍 Entry: <b>{entry}</b>\n"
        f"🛑 SL: <b>{sl}</b>\n"
        f"🎯 TP: <b>{tp}</b>\n\n"
        f"{action}"
    )


async def run_outcome_tracker(bot, interval_seconds: int = CHECK_INTERVAL_SECONDS):
    logger.info(f"Outcome tracker started  checking every {interval_seconds}s")

    while True:
        try:
            async with AsyncSessionLocal() as session:
                open_signals = await get_open_signals(session)

            if not open_signals:
                continue

            logger.info(f"Checking {len(open_signals)} open signals")

            for signal in open_signals:
                try:
                    loop  = asyncio.get_running_loop()
                    quote = await loop.run_in_executor(None, lambda s=signal.symbol: get_live_quote(s))

                    outcome, price = _check_quote_outcome(signal, quote)
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

        await asyncio.sleep(interval_seconds)
