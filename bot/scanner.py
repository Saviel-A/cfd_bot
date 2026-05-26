"""
Scanner: runs on a loop, scans the owner's watchlist, broadcasts BUY/SELL
signals to the configured channel.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from bot.config import cfg, SIGNAL_CFG, RISK_CFG, COUNTER_TREND_RISK_CFG
from bot.db.session import AsyncSessionLocal
from bot.db.repositories.watchlist_repo import get_watchlist
from bot.db.repositories.signal_repo import get_last_signal_for_symbol, save_signal
from bot.formatter import format_signal_message, format_market_closed_message
from src.instruments import load_instrument_cfg, get_display_name
from src.data_fetcher import fetch_ohlcv, get_live_price
from src.indicators import compute_all
from src.signal_engine import generate_signal
from src.risk_manager import calculate_trade
from src.market_pressure import analyze_market_pressure, should_block_by_pressure
from src.chart import generate_chart
from src.calendar import check_news_risk
from src.trading_hours import _is_open, symbol_market_status
from src.signal_profiles import signal_profile, stale_candle_reason

logger = logging.getLogger(__name__)

_IL = ZoneInfo("Asia/Jerusalem")


def _seconds_until_next_hour() -> float:
    """Seconds until the next exact UTC hour (e.g. 15:00:00, 16:00:00)."""
    now  = datetime.now(timezone.utc)
    next_h = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return (next_h - now).total_seconds()


def _market_open_now() -> bool:
    """True if forex/metals market is open right now (covers most watchlist instruments)."""
    return _is_open("forex", datetime.now(timezone.utc))


async def _fetch(ticker: str, timeframe: str, lookback: int = 200):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: fetch_ohlcv(ticker, timeframe=timeframe, lookback=lookback)
    )


async def scan_symbol(symbol: str) -> dict | None:
    try:
        cfg_inst = load_instrument_cfg(symbol)
        ticker   = cfg_inst.get("ticker", symbol)
        profile  = signal_profile(symbol)

        df     = await _fetch(ticker, profile["entry_timeframe"])
        df_htf = await _fetch(ticker, profile["htf_timeframe"], lookback=300)
        df     = compute_all(df, cfg_inst)

        signal = generate_signal(
            df,
            SIGNAL_CFG,
            cfg_inst,
            df_htf=df_htf,
            htf_label=profile["htf_label"],
            entry_label=profile["entry_label"],
        )
        stale_reason = stale_candle_reason(df, profile["entry_timeframe"], symbol)
        if stale_reason:
            signal.direction = "HOLD"
            signal.reason = stale_reason
        pressure = analyze_market_pressure(df)

        if signal.direction in ("BUY", "SELL") and should_block_by_pressure(symbol, signal, pressure):
            signal.reason = f"{signal.direction} blocked: {pressure.reason}"
            signal.direction = "HOLD"

        atr   = float(df.iloc[-1].get("atr", 0) or 0)
        trade = None
        if signal.direction in ("BUY", "SELL"):
            risk = COUNTER_TREND_RISK_CFG if signal.is_counter_trend else RISK_CFG
            trade = calculate_trade(signal.direction, signal.current_price, atr, risk, symbol=symbol)
            if trade is None:
                signal.reason = f"{signal.direction} blocked: risk levels unavailable"
                signal.direction = "HOLD"

        return {
            "symbol":       symbol,
            "ticker":       ticker,
            "signal":       signal,
            "trade":        trade,
            "atr":          atr,
            "market_pressure": pressure.as_dict(),
            "display_name": get_display_name(symbol),
        }
    except Exception as e:
        logger.error(f"Error scanning {symbol}: {e}")
        return None


async def _broadcast_market_closed(bot, symbol: str, loop):
    """Send a market-closed chart update to the channel."""
    if not cfg.BROADCAST_CHANNEL_ID:
        return False
    try:
        display = get_display_name(symbol)
        live_price = None
        try:
            live_price = await loop.run_in_executor(None, lambda s=symbol: get_live_price(s))
        except Exception:
            pass
        msg = format_market_closed_message(display, live_price, symbol=symbol)
        buf = await loop.run_in_executor(None, lambda s=symbol, p=live_price: generate_chart(s, live_price=p))
        from aiogram.types import BufferedInputFile
        await bot.send_photo(
            cfg.BROADCAST_CHANNEL_ID,
            BufferedInputFile(buf.read(), filename=f"{symbol}.png"),
            caption=msg,
            parse_mode="HTML",
        )
        logger.info(f"Market closed update sent: {symbol}")
        return True
    except Exception as e:
        logger.error(f"Market closed broadcast failed for {symbol}: {e}")
        return False


async def _broadcast_signal(bot, symbol: str, result: dict):
    """Fetch live price, recalculate trade, and send chart+signal to channel."""
    if not cfg.BROADCAST_CHANNEL_ID:
        logger.warning("BROADCAST_CHANNEL_ID not set: signal not sent")
        return False

    signal = result["signal"]
    trade  = result["trade"]
    atr    = result.get("atr", 0)

    loop = asyncio.get_running_loop()

    # News risk check
    news_risk, news_events = await loop.run_in_executor(
        None, lambda s=symbol: check_news_risk(s)
    )
    if news_risk == "BLOCK":
        titles = ", ".join(e["title"] for e in news_events)
        logger.info(f"{symbol}: signal BLOCKED by high-impact news: {titles}")
        return False

    if trade is None:
        logger.info(f"{symbol}: signal blocked because risk levels are unavailable")
        return False

    # Use pre-fetched live price if available, otherwise fetch now
    live_price = result.get("live_price")
    if not live_price:
        try:
            live_price = await loop.run_in_executor(None, lambda s=symbol: get_live_price(s))
        except Exception:
            pass
    if live_price and atr > 0:
        risk = COUNTER_TREND_RISK_CFG if signal.is_counter_trend else RISK_CFG
        trade = calculate_trade(signal.direction, live_price, atr, risk, symbol=symbol)

    msg = format_signal_message(
        result["display_name"], signal, trade,
        symbol=symbol, live_price=live_price, news_risk=news_risk, news_events=news_events,
        market_pressure=result.get("market_pressure"),
    )

    try:
        # Pass live_price so chart title matches alert entry exactly
        buf = await loop.run_in_executor(None, lambda s=symbol, p=live_price: generate_chart(s, live_price=p))
        from aiogram.types import BufferedInputFile
        await bot.send_photo(
            cfg.BROADCAST_CHANNEL_ID,
            BufferedInputFile(buf.read(), filename=f"{symbol}.png"),
            caption=msg,
            parse_mode="HTML",
        )
        logger.info(f"Signal sent (photo): {symbol} {signal.direction}")
        return True
    except Exception as e:
        logger.error(f"Chart failed for {symbol}: {e}: falling back to text")
        try:
            await bot.send_message(cfg.BROADCAST_CHANNEL_ID, msg, parse_mode="HTML")
            logger.info(f"Signal sent (text): {symbol} {signal.direction}")
            return True
        except Exception as e2:
            logger.error(f"Broadcast FAILED for {symbol}: {e2}")
            return False


async def _duplicate_suppression_reason(symbol: str, direction: str) -> str | None:
    """Return a reason when a same-direction signal is still inside cooldown."""
    async with AsyncSessionLocal() as session:
        last = await get_last_signal_for_symbol(session, symbol)
        if not last or last.direction != direction:
            return None

        fired = last.fired_at if last.fired_at.tzinfo else last.fired_at.replace(tzinfo=timezone.utc)
        cooldown = timedelta(minutes=signal_profile(symbol)["duplicate_cooldown_minutes"])
        remaining = cooldown - (datetime.now(timezone.utc) - fired)
        if remaining <= timedelta(0):
            return None

        minutes = max(1, int(remaining.total_seconds() // 60))
        return f"{direction} already sent. Cooldown: {minutes}m left"


async def _prepare_live_trade(symbol: str, result: dict) -> tuple[float | None, object | None]:
    signal = result["signal"]
    trade = result["trade"]
    atr = result.get("atr", 0)
    loop = asyncio.get_running_loop()

    live_price = None
    try:
        live_price = await loop.run_in_executor(None, lambda s=symbol: get_live_price(s))
    except Exception:
        pass

    if live_price and atr > 0:
        risk = COUNTER_TREND_RISK_CFG if signal.is_counter_trend else RISK_CFG
        trade = calculate_trade(signal.direction, live_price, atr, risk, symbol=symbol)

    return live_price, trade


async def _save_sent_signal(symbol: str, result: dict, live_price, trade):
    signal = result["signal"]
    entry_for_db = live_price if live_price else signal.current_price
    async with AsyncSessionLocal() as session:
        await save_signal(session, {
            "symbol":           symbol,
            "direction":        signal.direction,
            "timeframe":        signal_profile(symbol)["entry_timeframe"],
            "entry_price":      entry_for_db,
            "stop_loss":        trade.stop_loss if trade else entry_for_db,
            "tp1":              trade.tp1 if trade else entry_for_db,
            "tp2":              trade.tp2 if trade else entry_for_db,
            "tp3":              trade.tp3 if trade else entry_for_db,
            "sl_distance":      trade.sl_distance if trade else None,
            "atr":              trade.atr if trade else None,
            "confluence_score": signal.strength,
            "confluence_total": signal.total_indicators,
            "indicator_votes":  signal.details,
        })


async def broadcast_signal_if_allowed(bot, symbol: str, result: dict) -> tuple[bool, str | None]:
    """Broadcast and persist a signal using the same rules for all scan paths."""
    signal = result["signal"]
    if signal.direction not in ("BUY", "SELL"):
        return False, signal.reason or "No clean setup"

    duplicate_reason = await _duplicate_suppression_reason(symbol, signal.direction)
    if duplicate_reason:
        logger.info(f"{symbol}: {duplicate_reason}")
        return False, duplicate_reason

    live_price, trade = await _prepare_live_trade(symbol, result)
    result["live_price"] = live_price
    result["trade"] = trade

    sent = await _broadcast_signal(bot, symbol, result)
    if sent:
        await _save_sent_signal(symbol, result, live_price, trade)
        return True, None
    return False, "Broadcast failed"


async def run_scan_loop(bot, interval_minutes: int = 60):
    logger.info(f"Scanner started: scanning every {interval_minutes}m")

    _prev_market_open: bool | None = None

    while True:
        now_utc  = datetime.now(timezone.utc)
        now_il   = now_utc.astimezone(_IL)
        time_str = now_il.strftime("%d %B  %H:%M")

        market_open = _market_open_now()

        try:
            # Market open / close notifications (once per transition)
            if cfg.BROADCAST_CHANNEL_ID:
                if _prev_market_open is False and market_open:
                    await bot.send_message(
                        cfg.BROADCAST_CHANNEL_ID,
                        f"🟢 <b>Market Open</b>\n"
                        f"Israel time: <b>{time_str}</b>",
                        parse_mode="HTML",
                    )
                elif _prev_market_open is True and not market_open:
                    await bot.send_message(
                        cfg.BROADCAST_CHANNEL_ID,
                        f"🔴 <b>Market Closed</b>\n"
                        f"No new alerts until market reopens.\n"
                        f"Israel time: <b>{time_str}</b>",
                        parse_mode="HTML",
                    )

            _prev_market_open = market_open

            if not market_open:
                logger.info(f"Market closed at {time_str}, skipping scan")
            else:
                # Scan all symbols
                async with AsyncSessionLocal() as session:
                    watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)

                if not watchlist:
                    logger.info("Watchlist empty: skipping scan")
                else:
                    logger.info(f"Scanning {len(watchlist)} symbols at {time_str}")
                    loop = asyncio.get_running_loop()
                    for symbol in watchlist:
                        try:
                            symbol_open, _ = symbol_market_status(symbol)
                            if not symbol_open:
                                continue

                            result = await scan_symbol(symbol)
                            if not result:
                                logger.info(f"{symbol}: scan failed")
                                continue
                            if result["signal"].direction not in ("BUY", "SELL"):
                                reason = result["signal"].reason or "No clean setup"
                                logger.info(f"{symbol}: no signal - {reason}")
                                continue

                            sent, reason = await broadcast_signal_if_allowed(bot, symbol, result)
                            if not sent and reason:
                                logger.info(f"{symbol}: no broadcast - {reason}")

                        except Exception as e:
                            logger.error(f"Error processing {symbol}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Scanner loop error: {e}", exc_info=True)

        await asyncio.sleep(interval_minutes * 60)
