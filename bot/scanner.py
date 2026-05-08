"""
Scanner: runs on a loop, scans the owner's watchlist, broadcasts BUY/SELL
signals to the configured channel.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from bot.config import cfg, SIGNAL_CFG, RISK_CFG
from bot.db.session import AsyncSessionLocal
from bot.db.repositories.watchlist_repo import get_watchlist
from bot.db.repositories.signal_repo import get_last_signal_for_symbol, save_signal
from bot.formatter import format_signal_message, format_market_closed_message
from src.instruments import load_instrument_cfg, get_display_name
from src.data_fetcher import fetch_ohlcv, get_live_price
from src.indicators import compute_all
from src.signal_engine import generate_signal
from src.risk_manager import calculate_trade
from src.market_pressure import analyze_market_pressure, pressure_confirms
from src.chart import generate_chart
from src.calendar import check_news_risk
from src.trading_hours import _is_open, symbol_market_status

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

        df     = await _fetch(ticker, cfg.DEFAULT_TIMEFRAME)
        df_htf = await _fetch(ticker, cfg.HTF_TIMEFRAME, lookback=300)
        df     = compute_all(df, cfg_inst)

        signal = generate_signal(df, SIGNAL_CFG, cfg_inst, df_htf=df_htf)
        pressure = analyze_market_pressure(df)

        if signal.direction in ("BUY", "SELL") and not pressure_confirms(signal.direction, pressure):
            signal.reason = f"{signal.direction} blocked: {pressure.reason}"
            signal.direction = "HOLD"

        atr   = float(df.iloc[-1].get("atr", 0) or 0)
        trade = None
        if signal.direction in ("BUY", "SELL"):
            trade = calculate_trade(signal.direction, signal.current_price, atr, RISK_CFG)
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
        trade = calculate_trade(signal.direction, live_price, atr, RISK_CFG)

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


async def run_scan_loop(bot, interval_minutes: int = 60):
    logger.info(f"Scanner started: aligning to top of hour, then firing every {interval_minutes}m")

    # Wait until the next top-of-hour so scans align with 1H candle closes
    wait = _seconds_until_next_hour()
    logger.info(f"Scanner waiting {wait:.0f}s until next hour mark")
    await asyncio.sleep(wait)

    _prev_market_open: bool | None = None

    while True:
        now_utc  = datetime.now(timezone.utc)
        now_il   = now_utc.astimezone(_IL)
        time_str = now_il.strftime("%d %B  %H:%M")

        market_open = _market_open_now()

        try:
            # Market open / close notifications
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
                        f"No new Forex/Gold/Energy alerts.\n"
                        f"Israel time: <b>{time_str}</b>",
                        parse_mode="HTML",
                    )

            _prev_market_open = market_open

            # Scan all symbols
            async with AsyncSessionLocal() as session:
                watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)

            if not watchlist:
                logger.info("Watchlist empty: skipping scan")
            else:
                logger.info(f"Scanning {len(watchlist)} symbols at {time_str} (market {'open' if market_open else 'closed'})")
                loop = asyncio.get_running_loop()
                for symbol in watchlist:
                    try:
                        symbol_open, _ = symbol_market_status(symbol)
                        if not symbol_open:
                            # Symbol market closed — broadcast chart with last price
                            await _broadcast_market_closed(bot, symbol, loop)
                            continue

                        result = await scan_symbol(symbol)
                        if not result or result["signal"].direction not in ("BUY", "SELL"):
                            continue

                        signal = result["signal"]
                        atr    = result.get("atr", 0)

                        # Suppress duplicate same-direction signal within 4h
                        async with AsyncSessionLocal() as session:
                            last = await get_last_signal_for_symbol(session, symbol)
                            if last and last.direction == signal.direction:
                                fired = last.fired_at if last.fired_at.tzinfo else last.fired_at.replace(tzinfo=timezone.utc)
                                if datetime.now(timezone.utc) - fired < timedelta(hours=4):
                                    logger.info(f"{symbol}: {signal.direction} already sent within 4h, suppressed")
                                    continue

                        # Fetch live price and recalculate trade
                        live_price = None
                        try:
                            live_price = await loop.run_in_executor(None, lambda s=symbol: get_live_price(s))
                        except Exception:
                            pass
                        trade = result["trade"]
                        if live_price and atr > 0:
                            trade = calculate_trade(signal.direction, live_price, atr, RISK_CFG)

                        # Pass pre-fetched live_price so _broadcast_signal doesn't fetch again
                        result["live_price"] = live_price
                        result["trade"]      = trade
                        sent = await _broadcast_signal(bot, symbol, result)
                        if sent:
                            entry_for_db = live_price if live_price else signal.current_price
                            async with AsyncSessionLocal() as session:
                                await save_signal(session, {
                                    "symbol":           symbol,
                                    "direction":        signal.direction,
                                    "timeframe":        cfg.DEFAULT_TIMEFRAME,
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

                    except Exception as e:
                        logger.error(f"Error processing {symbol}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Scanner loop error: {e}", exc_info=True)

        await asyncio.sleep(_seconds_until_next_hour())
