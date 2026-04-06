"""
Production scanner — scans all users' watchlists, fires signals to each user,
saves to DB, avoids duplicate alerts.
"""

import asyncio
import logging

from bot.config import cfg  # noqa: E402
from bot.db.session import AsyncSessionLocal
from bot.db.repositories.user_repo import get_all_active_users
from bot.db.repositories.settings_repo import get_settings
from bot.db.repositories.watchlist_repo import get_watchlist
from bot.db.repositories.signal_repo import (
    get_last_signal_for_symbol, save_signal, record_delivery
)
from bot.formatter import format_signal_message
from src.instruments import load_instrument_cfg, get_display_name
from src.data_fetcher import fetch_ohlcv
from src.indicators import compute_all
from src.signal_engine import generate_signal
from src.risk_manager import calculate_trade

logger = logging.getLogger(__name__)


async def _fetch(ticker: str, timeframe: str, lookback: int = 200):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: fetch_ohlcv(ticker, timeframe=timeframe, lookback=lookback)
    )


async def scan_symbol(symbol: str, settings) -> dict | None:
    try:
        cfg_inst = load_instrument_cfg(symbol)
        ticker   = cfg_inst.get("ticker", symbol)
        tf       = str(settings.timeframe)
        htf      = str(settings.htf_timeframe)

        df     = await _fetch(ticker, tf)
        df_htf = await _fetch(ticker, htf, lookback=300)

        df = compute_all(df, cfg_inst)

        signal_settings = {
            "signals": {
                "min_confluence": int(settings.min_confluence),
                "indicators": {
                    "ema_cross": True, "rsi": True,
                    "macd": True, "bollinger": True, "atr": True,
                },
            }
        }

        signal = generate_signal(df, signal_settings, cfg_inst, df_htf=df_htf)
        atr    = float(df.iloc[-1].get("atr", 0) or 0)
        trade  = None

        if signal.direction in ("BUY", "SELL"):
            risk_cfg = {
                "account_balance":  float(settings.account_balance),
                "risk_percent":     float(settings.risk_percent),
                "sl_atr_multiplier": float(settings.sl_atr_multiplier),
                "rr1": float(settings.rr1),
                "rr2": float(settings.rr2),
                "rr3": float(settings.rr3),
            }
            trade = calculate_trade(signal.direction, signal.current_price, atr, risk_cfg)

        return {
            "symbol":       symbol,
            "signal":       signal,
            "trade":        trade,
            "display_name": get_display_name(symbol),
            "timeframe":    tf,
        }
    except Exception as e:
        logger.error(f"Error scanning {symbol}: {e}")
        return None


async def run_scan_loop(bot, interval_minutes: int = 60):
    logger.info(f"Scanner started — interval {interval_minutes}m")

    while True:
        try:
            # Phase 1 — collect watchlists from DB, then close session before I/O
            symbol_users: dict[str, list[int]] = {}
            user_settings: dict[int, object] = {}

            async with AsyncSessionLocal() as session:
                users = await get_all_active_users(session)
                for user in users:
                    if user.id != cfg.OWNER_CHAT_ID and not user.is_premium:
                        continue
                    settings = await get_settings(session, user.id)
                    if not settings.alerts_enabled:
                        continue
                    user_settings[user.id] = settings
                    watchlist = await get_watchlist(session, user.id)
                    for sym in watchlist:
                        symbol_users.setdefault(sym, []).append(user.id)

            if not symbol_users:
                await asyncio.sleep(interval_minutes * 60)
                continue

            logger.info(f"Scanning {len(symbol_users)} unique symbols for {len(user_settings)} users")

            # Phase 2 — scan symbols (network I/O), session closed
            for symbol, user_ids in symbol_users.items():
                try:
                    settings = user_settings.get(user_ids[0])
                    if not settings:
                        continue

                    result = await scan_symbol(symbol, settings)
                    if not result or result["signal"].direction not in ("BUY", "SELL"):
                        continue

                    signal = result["signal"]
                    trade  = result["trade"]

                    # Phase 3 — short DB operations per signal
                    async with AsyncSessionLocal() as session:
                        last_sig = await get_last_signal_for_symbol(session, symbol)
                        if last_sig and last_sig.direction == signal.direction and last_sig.outcome == "OPEN":
                            logger.info(f"{symbol}: duplicate {signal.direction} suppressed")
                            continue

                        sig_record = await save_signal(session, {
                            "symbol":           symbol,
                            "direction":        signal.direction,
                            "timeframe":        result["timeframe"],
                            "entry_price":      signal.current_price,
                            "stop_loss":        trade.stop_loss if trade else signal.current_price,
                            "tp1":              trade.tp1 if trade else signal.current_price,
                            "tp2":              trade.tp2 if trade else signal.current_price,
                            "tp3":              trade.tp3 if trade else signal.current_price,
                            "sl_distance":      trade.sl_distance if trade else None,
                            "atr":              trade.atr if trade else None,
                            "confluence_score": signal.strength,
                            "confluence_total": signal.total_indicators,
                            "indicator_votes":  signal.details,
                        })

                    msg = format_signal_message(result["display_name"], signal, trade)

                    # Broadcast to channel if configured
                    if cfg.BROADCAST_CHANNEL_ID:
                        try:
                            await bot.send_message(cfg.BROADCAST_CHANNEL_ID, msg, parse_mode="HTML")
                            logger.info(f"Channel broadcast: {symbol} {signal.direction}")
                        except Exception as e:
                            logger.error(f"Channel broadcast failed: {e}")

                    # DM each subscribed premium user
                    for user_id in user_ids:
                        try:
                            await bot.send_message(user_id, msg, parse_mode="HTML")
                            async with AsyncSessionLocal() as session:
                                await record_delivery(session, sig_record.id, user_id)
                            logger.info(f"Alert sent to {user_id}: {symbol} {signal.direction}")
                        except Exception as e:
                            logger.error(f"Failed to send to {user_id}: {e}")
                        await asyncio.sleep(0.1)

                except Exception as e:
                    logger.error(f"Error processing {symbol}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Scanner loop error: {e}", exc_info=True)

        await asyncio.sleep(interval_minutes * 60)
