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

_proximity_alert_sent: dict[str, datetime] = {}
_PROXIMITY_COOLDOWN = timedelta(minutes=30)


def _seconds_until_next_hour() -> float:
    """Seconds until the next exact UTC hour (e.g. 15:00:00, 16:00:00)."""
    now  = datetime.now(timezone.utc)
    next_h = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return (next_h - now).total_seconds()


def _market_open_now() -> bool:
    """True if forex/metals market is open right now (covers most watchlist instruments)."""
    return _is_open("forex", datetime.now(timezone.utc))


def _auto_session_suppression_reason(symbol: str) -> str | None:
    """Only alert during London + NY sessions (07:00-22:00 UTC).
    Deep Asian session (22:00-07:00 UTC) has negative expectancy for gold."""
    if symbol.upper() not in {"XAUUSD", "GOLD"}:
        return None
    now_utc = datetime.now(timezone.utc)
    hour_utc = now_utc.hour
    if 7 <= hour_utc < 22:
        return None
    return "Gold alerts paused outside London/NY session (07:00-22:00 UTC)"


def _ema_bias(df) -> str:
    """EMA 20/50 trend direction — used for 4H confirmation gate."""
    if df is None or len(df) < 50:
        return "NEUTRAL"
    ema20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    if ema20 > ema50:
        return "BULLISH"
    if ema20 < ema50:
        return "BEARISH"
    return "NEUTRAL"


def _gold_quality_suppression_reason(
    symbol: str, signal, df, pressure, atr: float = 0,
    df_4h=None, df_daily=None,
) -> str | None:
    """Extra conservative filters for Gold alerts."""
    if symbol.upper() not in {"XAUUSD", "GOLD"} or signal.direction not in ("BUY", "SELL"):
        return None
    if signal.strength < 3:
        return "Gold requires at least 3/4 indicator confirmation"
    if df is None or len(df) < 2:
        return "Gold requires enough closed candles"

    last = df.iloc[-1]

    # Candle conviction: reject doji/indecision candles
    candle_range = abs(float(last["high"]) - float(last["low"]))
    body = abs(float(last["close"]) - float(last["open"]))
    if candle_range > 0 and body / candle_range < 0.35:
        return f"Gold entry rejected: indecision candle (body {body/candle_range:.0%} of range)"

    # Price vs EMA 21: close must be on the right side of the trend
    if "ema_21" in df.columns:
        ema21 = float(last.get("ema_21", 0) or 0)
        close = float(last["close"])
        if signal.direction == "BUY" and close < ema21:
            return "Gold entry rejected: price below EMA 21 on BUY signal"
        if signal.direction == "SELL" and close > ema21:
            return "Gold entry rejected: price above EMA 21 on SELL signal"

    # Volume: reject low-participation fake breakouts
    adx_now = float(last.get("adx", 0) or 0)
    if "volume" in df.columns and adx_now < 40:
        vol = float(last.get("volume", 0) or 0)
        avg_vol = float(df["volume"].tail(20).mean() or 0)
        if avg_vol > 0 and vol < avg_vol * 0.6:
            return "Gold entry rejected: low volume (below 60% of 20-bar average)"

    # 4H trend gate: 1H and 4H must not contradict each other.
    # After a big drop the 1H EMA stays bearish for hours into the recovery —
    # 4H catches the flip earlier and blocks premature SELL re-entries.
    if df_4h is not None and len(df_4h) >= 50:
        bias_4h = _ema_bias(df_4h)
        if signal.direction == "SELL" and bias_4h == "BULLISH":
            return "Gold entry rejected: 4H trend is bullish, 1H SELL not confirmed"
        if signal.direction == "BUY" and bias_4h == "BEARISH":
            return "Gold entry rejected: 4H trend is bearish, 1H BUY not confirmed"

    # ADR exhaustion: if today's session has already consumed 88%+ of the
    # 10-day average daily range, institutional pressure is likely spent.
    # Block trend-continuation entries — mean-reversion probability is high.
    if df_daily is not None and len(df_daily) >= 12:
        completed = df_daily.iloc[:-1].tail(10)
        adr_10 = float((completed["high"] - completed["low"]).mean())
        today = df_daily.iloc[-1]
        today_range = float(today["high"]) - float(today["low"])
        if adr_10 > 0 and today_range / adr_10 >= 0.88:
            return (
                f"Gold entry rejected: daily range exhausted "
                f"({today_range:.0f}pts vs ADR {adr_10:.0f}pts)"
            )

    return None


def _swing_sl_distance(direction: str, df) -> float | None:
    """Distance from last close to the nearest swing high/low over 8 candles."""
    if df is None or len(df) < 5:
        return None
    recent = df.iloc[-5:]
    close = float(df.iloc[-1]["close"])
    if direction == "BUY":
        return close - float(recent["low"].min())
    else:
        return float(recent["high"].max()) - close


def _adx_suppression_reason(symbol: str, df) -> str | None:
    """Block signals when the market has no directional trend (ADX too low)."""
    if df is None or "adx" not in df.columns:
        return None
    adx = float(df.iloc[-1].get("adx", 0) or 0)
    if adx < 20:
        return f"Market is ranging (ADX {adx:.0f} < 20)"
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

        # Gold: fetch 4H and daily for extra quality gates (4H trend + ADR exhaustion)
        df_4h = None
        df_daily = None
        if symbol.upper() in {"XAUUSD", "GOLD"}:
            df_4h, df_daily = await asyncio.gather(
                _fetch(ticker, "4h", lookback=200),
                _fetch(ticker, "1d", lookback=30),
            )

        df = compute_all(df, cfg_inst)

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
            if signal.is_counter_trend and symbol.upper() in {"XAUUSD", "GOLD"}:
                signal.reason = f"{signal.direction} blocked: counter-trend to {signal.htf_bias.lower()} 1H bias"
            else:
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

            quality_reason = _gold_quality_suppression_reason(symbol, signal, df, pressure, atr=atr, df_4h=df_4h, df_daily=df_daily)
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

            if symbol.upper() in {"XAUUSD", "GOLD"}:
                swing_dist = _swing_sl_distance(signal.direction, df)
                if swing_dist is not None:
                    pip = get_pip_size(symbol)
                    risk_temp = _risk_config_for_signal(symbol, signal)
                    sl_max_pts = float(risk_temp.get("sl_max", 10)) * 1.8
                    swing_pts = swing_dist / pip
                    if swing_pts > sl_max_pts:
                        signal.reason = f"{signal.direction} blocked: structure SL {swing_pts:.1f}pts (max {sl_max_pts:.0f}pts)"
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


async def _send_proximity_alert(bot, symbol: str, result: dict) -> None:
    """Send owner-only DM when 3/4 indicators align but signal hasn't fired yet."""
    if not cfg.OWNER_CHAT_ID:
        return

    signal = result["signal"]
    votes  = signal.details or {}
    bear   = sum(1 for v in votes.values() if v == -1)
    bull   = sum(1 for v in votes.values() if v == 1)
    pressure = result.get("market_pressure", {})

    if signal.htf_bias == "BEARISH" and bear == 3:
        approaching = "SELL"
        count = bear
    elif signal.htf_bias == "BULLISH" and bull == 3:
        approaching = "BUY"
        count = bull
    else:
        return

    last_sent = _proximity_alert_sent.get(symbol)
    if last_sent and datetime.now(timezone.utc) - last_sent < _PROXIMITY_COOLDOWN:
        return
    _proximity_alert_sent[symbol] = datetime.now(timezone.utc)

    dot    = "📉" if approaching == "SELL" else "📈"
    filled = "🔴" if approaching == "SELL" else "🟢"
    bar    = filled * count + "⬜" * (4 - count)

    ind_lines = "\n".join(
        f"  {'✅' if v == (-1 if approaching == 'SELL' else 1) else '❌'} {name}: {'aligned' if v == (-1 if approaching == 'SELL' else 1) else 'not yet'}"
        for name, v in votes.items()
    )

    buy_pct  = pressure.get("buy_pct", 0)
    sell_pct = pressure.get("sell_pct", 0)
    rsi_val  = float((result.get("signal").details or {}).get("RSI", 0) or 0)

    msg = (
        f"⚠️ <b>{symbol} — Almost Ready</b>\n\n"
        f"{dot} <b>{approaching}</b> signal: {bar} 3/4 indicators\n\n"
        f"<b>Indicators:</b>\n{ind_lines}\n\n"
        f"💹 Pressure: buy <b>{buy_pct:.0f}%</b>  sell <b>{sell_pct:.0f}%</b>\n"
        f"📍 HTF bias: <b>{signal.htf_bias}</b>\n\n"
        f"<i>One more indicator needed to fire the alert.</i>"
    )

    try:
        await bot.send_message(cfg.OWNER_CHAT_ID, msg, parse_mode="HTML")
        logger.info(f"Proximity alert sent to owner: {symbol} {approaching} 3/4")
    except Exception as e:
        logger.error(f"Proximity alert failed for {symbol}: {e}")


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
                                await _send_proximity_alert(bot, symbol, result)
                                continue

                            sent, reason = await broadcast_signal_if_allowed(bot, symbol, result)
                            if not sent and reason:
                                logger.info(f"{symbol}: no broadcast - {reason}")

                        except Exception as e:
                            logger.error(f"Error processing {symbol}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Scanner loop error: {e}", exc_info=True)

        await asyncio.sleep(interval_minutes * 60)
