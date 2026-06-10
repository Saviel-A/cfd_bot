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
from bot.db.repositories.signal_repo import get_active_signal_for_symbol, get_last_signal_for_symbol, save_signal
from bot.formatter import format_signal_message, format_market_closed_message
from src.instruments import load_instrument_cfg, get_display_name
from src.data_fetcher import fetch_ohlcv, get_live_price
from src.indicators import compute_all
from src.signal_engine import generate_signal
from src.risk_manager import calculate_trade, get_pip_size
from src.market_pressure import analyze_market_pressure, should_block_by_pressure
from src.chart import generate_chart
from src.calendar import check_news_risk
from src.trading_hours import _is_open, symbol_market_status
from src.signal_profiles import signal_profile, stale_candle_reason
from src.gold_strategy import apply_gold_momentum

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


def _auto_session_suppression_reason(symbol: str) -> str | None:
    """Avoid opening Gold alerts during overnight liquidity."""
    if symbol.upper() not in {"XAUUSD", "GOLD"}:
        return None
    now_il = datetime.now(timezone.utc).astimezone(_IL)
    minutes = now_il.hour * 60 + now_il.minute
    start = 8 * 60
    end = 22 * 60 + 30
    if start <= minutes <= end:
        return None
    return "Gold auto alerts paused outside 08:00-22:30 Israel time"


def _gold_quality_suppression_reason(symbol: str, signal, df, pressure) -> str | None:
    """Extra conservative filters for Gold alerts."""
    if symbol.upper() not in {"XAUUSD", "GOLD"} or signal.direction not in ("BUY", "SELL"):
        return None
    if signal.strength < 3:
        return "Gold requires at least 3/4 indicator confirmation"
    if df is None or len(df) < 2:
        return "Gold requires enough closed candles"

    # Bollinger squeeze: bands must be expanding — entering a squeeze means entering chop
    if "bb_bandwidth" in df.columns and len(df) >= 3:
        bw_now  = float(df["bb_bandwidth"].iloc[-1] or 0)
        bw_prev = float(df["bb_bandwidth"].iloc[-2] or 0)
        if bw_now < bw_prev and bw_now > 0:
            return "Bollinger bands squeezing — wait for breakout"

    return None


def _adx_suppression_reason(symbol: str, df) -> str | None:
    """Block signals when the market has no directional trend (ADX too low)."""
    if df is None or "adx" not in df.columns:
        return None
    adx = float(df.iloc[-1].get("adx", 0) or 0)
    if adx < 15:
        return f"Market is ranging (ADX {adx:.0f})"
    return None


def _risk_config_for_signal(symbol: str, signal) -> dict:
    return COUNTER_TREND_RISK_CFG if signal.is_counter_trend else RISK_CFG


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
        apply_gold_momentum(symbol, signal, pressure)

        if signal.direction in ("BUY", "SELL") and should_block_by_pressure(symbol, signal, pressure):
            signal.reason = f"{signal.direction} blocked: {pressure.reason}"
            signal.direction = "HOLD"

        atr   = float(df.iloc[-1].get("atr", 0) or 0)
        trade = None
        if signal.direction in ("BUY", "SELL"):
            adx_reason = _adx_suppression_reason(symbol, df)
            if adx_reason:
                signal.reason = f"{signal.direction} blocked: {adx_reason}"
                signal.direction = "HOLD"
                return {
                    "symbol":          symbol,
                    "ticker":          ticker,
                    "signal":          signal,
                    "trade":           None,
                    "atr":             atr,
                    "market_pressure": pressure.as_dict(),
                    "display_name":    get_display_name(symbol),
                }

            quality_reason = _gold_quality_suppression_reason(symbol, signal, df, pressure)
            if quality_reason:
                signal.reason = f"{signal.direction} blocked: {quality_reason}"
                signal.direction = "HOLD"
                return {
                    "symbol":       symbol,
                    "ticker":       ticker,
                    "signal":       signal,
                    "trade":        None,
                    "atr":          atr,
                    "market_pressure": pressure.as_dict(),
                    "display_name": get_display_name(symbol),
                }

            risk = _risk_config_for_signal(symbol, signal)
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
        risk = _risk_config_for_signal(symbol, signal)
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
    """Return a reason when a recent signal should suppress auto broadcast."""
    async with AsyncSessionLocal() as session:
        last = await get_last_signal_for_symbol(session, symbol)
        if not last:
            return None

        fired = last.fired_at if last.fired_at.tzinfo else last.fired_at.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - fired

        cooldown = timedelta(minutes=signal_profile(symbol)["duplicate_cooldown_minutes"])
        if last.direction != direction:
            remaining = cooldown - elapsed
            if remaining > timedelta(0):
                minutes = max(1, int(remaining.total_seconds() // 60))
                return f"{direction} blocked: last alert was {last.direction}. Wait {minutes}m before flipping direction"
            return None

        remaining = cooldown - elapsed
        if remaining <= timedelta(0):
            return None

        minutes = max(1, int(remaining.total_seconds() // 60))
        return f"{direction} already sent. Cooldown: {minutes}m left"


async def _open_trade_suppression_reason(symbol: str) -> str | None:
    """Block auto alerts while an earlier trade is still open."""
    async with AsyncSessionLocal() as session:
        active = await get_active_signal_for_symbol(session, symbol)
        if not active:
            return None
        return f"{active.direction} trade still open. Waiting for TP or SL"


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
        risk = _risk_config_for_signal(symbol, signal)
        trade = calculate_trade(signal.direction, live_price, atr, risk, symbol=symbol)

    return live_price, trade


async def _save_sent_signal(symbol: str, result: dict, live_price, trade):
    if trade is None:
        logger.info(f"{symbol}: sent alert was not saved because trade levels are unavailable")
        return

    signal = result["signal"]
    entry_for_db = live_price if live_price else signal.current_price
    async with AsyncSessionLocal() as session:
        await save_signal(session, {
            "symbol":           symbol,
            "direction":        signal.direction,
            "timeframe":        signal_profile(symbol)["entry_timeframe"],
            "entry_price":      entry_for_db,
            "stop_loss":        trade.stop_loss,
            "tp":               trade.tp,
            "sl_distance":      trade.sl_distance,
            "atr":              trade.atr,
            "confluence_score": signal.strength,
            "confluence_total": signal.total_indicators,
            "indicator_votes":  signal.details,
        })


async def broadcast_signal_if_allowed(
    bot,
    symbol: str,
    result: dict,
    *,
    enforce_cooldown: bool = True,
) -> tuple[bool, str | None]:
    """Broadcast and persist a signal using the same rules for all scan paths."""
    signal = result["signal"]
    if signal.direction not in ("BUY", "SELL"):
        return False, signal.reason or "No clean setup"

    if enforce_cooldown:
        session_reason = _auto_session_suppression_reason(symbol)
        if session_reason:
            logger.info(f"{symbol}: {session_reason}")
            return False, session_reason

        open_reason = await _open_trade_suppression_reason(symbol)
        if open_reason:
            logger.info(f"{symbol}: {open_reason}")
            return False, open_reason

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
