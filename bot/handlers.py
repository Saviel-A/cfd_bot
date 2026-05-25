"""
Telegram handlers.
- Owner: command console with back navigation
- Subscribers: welcome -> subscribe -> pay Stars -> get channel invite
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, ChatJoinRequest, ErrorEvent
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.session import AsyncSessionLocal
from bot.db.repositories.user_repo import get_or_create_user, grant_premium_until
from bot.db.repositories.watchlist_repo import get_watchlist, add_symbol, remove_symbol
from bot.db.repositories.signal_repo import get_recent_signals, get_signal_stats, clear_all_signals, save_signal
from bot.formatter import (
    format_signal_message, format_hold_message, format_watchlist_message,
    format_history_message, format_stats_message, format_market_closed_message,
    format_scan_summary,
)
from src.instruments import load_instrument_cfg, get_display_name, get_symbol_label, get_ticker_for_symbol, CATEGORIES
from src.data_fetcher import fetch_ohlcv, get_live_price
from src.indicators import compute_all
from src.signal_engine import generate_signal
from src.risk_manager import calculate_trade
from src.market_pressure import analyze_market_pressure, pressure_confirms
from src.news import get_news, format_news_message
from src.trading_hours import get_hours_message, symbol_market_status
from src.calendar import get_calendar, format_calendar_message
from bot.config import cfg, SIGNAL_CFG, RISK_CFG
from src.chart import generate_chart

logger = logging.getLogger(__name__)
router = Router()

# Subscription plans
# 1 Star ≈ $0.013 USD
_PLANS = {
    "monthly":   {"label": "1 Month",  "price_usd": 99,  "stars": 7600,  "days": 30},
    "quarterly": {"label": "3 Months", "price_usd": 199, "stars": 15300, "days": 90},
    "lifetime":  {"label": "Lifetime", "price_usd": 299, "stars": 23000, "days": 0},
}


# Helpers
def _is_owner(user_id: int) -> bool:
    return user_id == cfg.OWNER_CHAT_ID


async def _check_owner(message: Message) -> bool:
    return _is_owner(message.from_user.id)


def _menu_markup():
    """Standard back-to-menu button."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Home", callback_data="home:menu")
    return builder.as_markup()


def _menu_markup_main():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔔 Check Signal", callback_data="home:signal")
    builder.button(text="📡 Scan Channel", callback_data="home:scan")
    builder.button(text="📉 Chart",         callback_data="home:chart")
    builder.button(text="📊 Stats",         callback_data="home:stats")
    builder.button(text="📜 History",      callback_data="home:history")
    builder.button(text="📋 Watchlist",    callback_data="home:watchlist")
    builder.button(text="🔎 Instruments",  callback_data="home:symbols")
    builder.button(text="➕ Add Symbol",   callback_data="home:symbols")
    builder.button(text="➖ Remove Symbol", callback_data="home:remove")
    builder.button(text="📰 News",         callback_data="home:market")
    builder.button(text="📅 Calendar",     callback_data="home:calendar")
    builder.button(text="🕐 Market Hours", callback_data="home:hours")
    builder.button(text="👥 Subscribers",  callback_data="home:users")
    builder.button(text="✉️ Invite Subscriber", callback_data="home:approve")
    builder.button(text="📲 Share Link",   callback_data="home:sharelink")
    builder.adjust(2)
    return builder.as_markup()


def _is_db_error(exc: Exception) -> bool:
    text = str(exc)
    return "Connect call failed" in text or "5432" in text or "asyncpg" in text


async def _send_error_notice(event: ErrorEvent) -> bool:
    exc = event.exception
    logger.exception("Unhandled bot update error", exc_info=exc)

    if _is_db_error(exc):
        text = (
            "⚠️ <b>Database Offline</b>\n\n"
            "The bot is online, but commands that need saved data cannot load right now.\n"
            "Start Docker/Postgres, then try again."
        )
        alert = "Database is offline. Start Docker/Postgres."
    else:
        text = "⚠️ <b>Command Failed</b>\n\nCheck the bot logs and try again."
        alert = "Command failed. Check logs."

    update = event.update
    callback = update.callback_query
    message = update.message

    try:
        if callback:
            await callback.answer(alert, show_alert=True)
            if callback.message:
                await callback.message.answer(text, parse_mode="HTML")
            return True
        if message:
            await message.answer(text, parse_mode="HTML")
            return True
    except Exception:
        logger.exception("Failed to send error notice")

    return True


router.errors.register(_send_error_notice)


def _symbol_buttons(builder: InlineKeyboardBuilder, symbols: list[str], prefix: str) -> None:
    for symbol in symbols:
        builder.button(text=get_symbol_label(symbol), callback_data=f"{prefix}:{symbol}")


# Bot username — set once at startup via set_bot_username()
_BOT_USERNAME: str | None = None

def set_bot_username(username: str):
    global _BOT_USERNAME
    _BOT_USERNAME = username


def _commands_text(name: str) -> str:
    return (
        f"🤖 <b>CFD Signal Bot Owner Console</b>\n\n"
        f"Welcome, <b>{name}</b>.\n\n"
        "🔔 <b>Signals</b>\n"
        "/signal XAUUSD - Check Signal\n"
        "/scan - Scan Channel\n"
        "/chart XAUUSD - Chart\n\n"
        "📊 <b>Stats</b>\n"
        "/stats - Stats\n"
        "/history - History\n\n"
        "📋 <b>Watchlist</b>\n"
        "/watchlist - Watchlist\n"
        "/symbols - Instruments\n"
        "/add XAUUSD - Add Symbol\n"
        "/remove XAUUSD - Remove Symbol\n\n"
        "📰 <b>Market Info</b>\n"
        "/market - News\n"
        "/calendar - Calendar\n"
        "/hours - Market Hours\n\n"
        "👥 <b>Subscribers</b>\n"
        "/users - Subscribers\n"
        "/approve - Invite Subscriber"
    )


async def _scan_symbol(symbol: str) -> dict:
    cfg_inst = load_instrument_cfg(symbol)
    ticker   = cfg_inst.get("ticker", symbol)

    loop   = asyncio.get_running_loop()
    df     = await loop.run_in_executor(None, lambda: fetch_ohlcv(ticker, timeframe=cfg.DEFAULT_TIMEFRAME, lookback=200))
    df_htf = await loop.run_in_executor(None, lambda: fetch_ohlcv(ticker, timeframe=cfg.HTF_TIMEFRAME, lookback=300))
    df     = compute_all(df, cfg_inst)

    signal = generate_signal(df, SIGNAL_CFG, cfg_inst, df_htf=df_htf)
    pressure = analyze_market_pressure(df)
    if signal.direction in ("BUY", "SELL") and not pressure_confirms(signal.direction, pressure):
        signal.reason = f"{signal.direction} blocked: {pressure.reason}"
        signal.direction = "HOLD"

    atr   = float(df.iloc[-1].get("atr", 0) or 0)
    trade = None
    if signal.direction in ("BUY", "SELL"):
        trade = calculate_trade(signal.direction, signal.current_price, atr, RISK_CFG, symbol=symbol)
        if trade is None:
            signal.reason = f"{signal.direction} blocked: risk levels unavailable"
            signal.direction = "HOLD"

    return {
        "symbol": symbol,
        "signal": signal,
        "trade": trade,
        "atr": atr,
        "market_pressure": pressure.as_dict(),
        "display_name": get_display_name(symbol),
    }


async def _send_closed_symbol_chart(target, symbol: str, edit_message=None):
    loop = asyncio.get_running_loop()
    live_price = None
    try:
        live_price = await loop.run_in_executor(None, lambda s=symbol: get_live_price(s))
    except Exception:
        pass
    text = format_market_closed_message(get_display_name(symbol), live_price, symbol=symbol)
    try:
        buf = await loop.run_in_executor(None, lambda s=symbol, p=live_price: generate_chart(s, live_price=p))
        from aiogram.types import BufferedInputFile
        if edit_message:
            await edit_message.delete()
        await target.answer_photo(
            BufferedInputFile(buf.read(), filename=f"{symbol}.png"),
            caption=text,
            parse_mode="HTML",
            reply_markup=_menu_markup(),
        )
    except Exception:
        if edit_message:
            await edit_message.edit_text(text, parse_mode="HTML", reply_markup=_menu_markup())
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=_menu_markup())


# /start & /help
@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message):
    name = message.from_user.first_name or message.from_user.username or "there"

    # Owner
    if _is_owner(message.from_user.id):
        await message.answer(
            _commands_text(name),
            parse_mode="HTML",
            reply_markup=_menu_markup_main(),
        )
        return

    try:
        async with AsyncSessionLocal() as session:
            await get_or_create_user(
                session,
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name,
            )
    except Exception as exc:
        logger.warning(f"Could not register user {message.from_user.id}: {exc}")

    # User: show subscription page (direct or via deep link)
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Subscribe Now", callback_data="sub:plans")
    await message.answer(
        f"👋 <b>Welcome, {name}</b>\n\n"
        "📡 <b>CFD Smart Signals</b>\n"
        "Private alerts for Forex, Gold and Indices.\n\n"
        "Each alert includes:\n"
        "Entry | SL | TP1 | TP2 | TP3\n"
        "7-10 pip SL cap\n"
        "1H setup + 4H trend check\n\n"
        "Subscribe to get channel access.",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


# Home callback router
@router.callback_query(F.data.startswith("home:"))
async def cb_home(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return

    action = callback.data.split(":")[1]
    await callback.answer()

    name = callback.from_user.first_name or callback.from_user.username or "there"

    # Menu (back button target)
    if action == "menu":
        try:
            await callback.message.edit_text(
                _commands_text(name),
                parse_mode="HTML",
                reply_markup=_menu_markup_main(),
            )
        except Exception:
            # Photo/media messages can't be edited to text — send fresh
            await callback.message.delete()
            await callback.message.answer(
                _commands_text(name),
                parse_mode="HTML",
                reply_markup=_menu_markup_main(),
            )
        return

    # Watchlist
    if action == "watchlist":
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
        if not watchlist:
            builder = InlineKeyboardBuilder()
            builder.button(text="➕ Add Symbol", callback_data="home:symbols")
            builder.button(text="🏠 Home", callback_data="home:menu")
            builder.adjust(1)
            await callback.message.edit_text(
                "📋 <b>Watchlist</b>\n\nEmpty. Add a symbol to start.",
                parse_mode="HTML", reply_markup=builder.as_markup(),
            )
            return

        await callback.message.edit_text("🔍 <b>Scanning watchlist...</b>", parse_mode="HTML")
        results = []
        for symbol in watchlist:
            try:
                results.append(await _scan_symbol(symbol))
            except Exception as e:
                results.append({"symbol": symbol, "error": str(e), "display_name": get_display_name(symbol), "signal": None})

        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Refresh",    callback_data="home:watchlist")
        builder.button(text="➕ Add Symbol", callback_data="home:symbols")
        builder.button(text="🏠 Home",       callback_data="home:menu")
        builder.adjust(2, 1)
        await callback.message.edit_text(
            format_watchlist_message(results), parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    # Signal
    elif action == "signal":
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
        builder = InlineKeyboardBuilder()
        _symbol_buttons(builder, watchlist, "scan_sym")
        builder.button(text="🔎 Instruments", callback_data="home:symbols")
        builder.button(text="🏠 Home",       callback_data="home:menu")
        builder.adjust(3)
        await callback.message.edit_text(
            "🔔 <b>Check Signal</b>\n\n" + ("Choose a symbol:" if watchlist else "Your watchlist is empty. Add a symbol first."),
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    # News
    elif action == "market":
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
        builder = InlineKeyboardBuilder()
        if watchlist:
            _symbol_buttons(builder, watchlist, "news_sym")
        else:
            builder.button(text="➕ Add Symbol", callback_data="home:symbols")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(3)
        await callback.message.edit_text(
            "📰 <b>Market News</b>\n\n" + ("Choose a symbol:" if watchlist else "Watchlist is empty. Add a symbol first."),
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    # Calendar
    elif action == "calendar":
        builder = InlineKeyboardBuilder()
        builder.button(text="📅 Today",     callback_data="calendar:today")
        builder.button(text="📅 This Week", callback_data="calendar:week")
        builder.button(text="🏠 Home",      callback_data="home:menu")
        builder.adjust(2, 1)
        await callback.message.edit_text(
            "📅 <b>Economic Calendar</b>\n\nChoose which events to show:",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    # Instruments
    elif action == "symbols":
        builder = InlineKeyboardBuilder()
        for category in CATEGORIES:
            builder.button(text=category, callback_data=f"cat:{category}")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(2)
        await callback.message.edit_text(
            "🔎 <b>Instruments</b>\n\nChoose a market category:",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    # Chart
    elif action == "chart":
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
        builder = InlineKeyboardBuilder()
        _symbol_buttons(builder, watchlist, "chart_sym")
        builder.button(text="🔎 Instruments", callback_data="home:symbols")
        builder.button(text="🏠 Home",       callback_data="home:menu")
        builder.adjust(3)
        await callback.message.edit_text(
            "📊 <b>Chart</b>\n\nChoose a symbol:",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    # Hours
    elif action == "hours":
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(1)
        await callback.message.edit_text(
            get_hours_message(), parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    # History
    elif action == "history":
        parts  = callback.data.split(":")
        sub    = parts[2] if len(parts) > 2 else ""

        if sub == "clear":
            async with AsyncSessionLocal() as session:
                count = await clear_all_signals(session)
            await callback.message.edit_text(
                f"🗑 <b>History Cleared</b>\n\nDeleted signals: <b>{count}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardBuilder().button(text="🏠 Home", callback_data="home:menu").as_markup(),
            )
        else:
            limit = int(sub) if sub.isdigit() else 20
            async with AsyncSessionLocal() as session:
                signals = await get_recent_signals(session, limit=limit)
            builder = InlineKeyboardBuilder()
            if limit <= 20:
                builder.button(text="Show 50",  callback_data="home:history:50")
            if limit <= 50:
                builder.button(text="Show 100", callback_data="home:history:100")
            builder.button(text="🗑 Clear History", callback_data="home:history:clear")
            builder.button(text="🏠 Home",          callback_data="home:menu")
            builder.adjust(2)
            await callback.message.edit_text(
                format_history_message(signals, limit=limit), parse_mode="HTML", reply_markup=builder.as_markup(),
            )

    # Stats
    elif action == "stats":
        async with AsyncSessionLocal() as session:
            stats = await get_signal_stats(session, limit=100)
        builder = InlineKeyboardBuilder()
        builder.button(text="📜 History", callback_data="home:history")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(2)
        await callback.message.edit_text(
            format_stats_message(stats), parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    # Subscribers
    elif action == "users":
        from bot.db.repositories.user_repo import get_all_users
        async with AsyncSessionLocal() as session:
            users = await get_all_users(session)

        non_owner = [u for u in users if u.id != cfg.OWNER_CHAT_ID]

        if not non_owner:
            builder = InlineKeyboardBuilder()
            builder.button(text="🏠 Home", callback_data="home:menu")
            await callback.message.edit_text(
                "👥 <b>Subscribers</b>\n\nNo users yet.\nAsk them to open the bot and tap Start.",
                parse_mode="HTML", reply_markup=builder.as_markup(),
            )
            return

        pending   = [u for u in non_owner if not getattr(u, 'is_invited', False)]
        approved  = [u for u in non_owner if getattr(u, 'is_invited', False)]

        lines = [f"👥 <b>Subscribers</b>", f"Total users: <b>{len(non_owner)}</b>", ""]
        lines.append(f"Pending: <b>{len(pending)}</b>")
        lines.append(f"Invited: <b>{len(approved)}</b>")
        lines.append("")
        lines.append("Use the buttons below to invite, kick, or delete a user.")
        for u in non_owner[:12]:
            status = "✅" if getattr(u, 'is_invited', False) else "⏳"
            label = u.first_name or u.username or str(u.id)
            if u.username:
                label += f" (@{u.username})"
            lines.append(f"{status} {label}")

        builder = InlineKeyboardBuilder()
        for u in non_owner:
            status = "✅" if getattr(u, 'is_invited', False) else "⏳"
            label = u.first_name or u.username or str(u.id)
            if u.username:
                label += f" (@{u.username})"
            builder.button(text=f"{status} {label}", callback_data=f"approve_user:{u.id}")
            builder.button(text="🚫 Kick", callback_data=f"kick_user:{u.id}")
            builder.button(text="🗑 Delete", callback_data=f"delete_user:{u.id}")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(*([3] * len(non_owner) + [1]))
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    # Remove
    elif action == "remove":
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
        builder = InlineKeyboardBuilder()
        for s in watchlist:
            builder.button(text=f"❌ {get_symbol_label(s)}", callback_data=f"remove_sym:{s}")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(3)
        text = "➖ <b>Remove Symbol</b>\n\nTap a symbol to remove it from the watchlist:" if watchlist else "➖ <b>Remove Symbol</b>\n\nWatchlist is empty."
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())

    # Approve
    elif action == "approve":
        from bot.db.repositories.user_repo import get_all_users
        async with AsyncSessionLocal() as session:
            users = await get_all_users(session)
        non_owner = [u for u in users if u.id != cfg.OWNER_CHAT_ID]
        builder = InlineKeyboardBuilder()
        for u in non_owner:
            label = f"✉️ {u.first_name or u.username or u.id}"
            builder.button(text=label, callback_data=f"approve_user:{u.id}")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(2)
        text = "✉️ <b>Invite Subscriber</b>\n\nTap a user to send them a private channel invite:" if non_owner else "✉️ <b>Invite Subscriber</b>\n\nNo users yet."
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())

    # Share Link
    elif action == "sharelink":
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Home", callback_data="home:menu")
        if _BOT_USERNAME:
            link      = f"https://t.me/{_BOT_USERNAME}"
            share_url = f"https://t.me/share/url?url={link}&text=Join%20CFD%20Smart%20Signals%20%E2%80%94%20professional%20trading%20signals%20for%20Forex%2C%20Gold%20%26%20Indices."
            builder = InlineKeyboardBuilder()
            builder.button(text="📲 Share with Friends", url=share_url)
            builder.button(text="🏠 Home", callback_data="home:menu")
            builder.adjust(1)
            await callback.message.edit_text(
                f"📲 <b>Subscriber Link</b>\n\n"
                f"<code>{link}</code>\n\n"
                "Tap the button below to share it directly in Telegram.",
                parse_mode="HTML", reply_markup=builder.as_markup(),
            )
        else:
            await callback.message.edit_text(
                "❌ Bot username not available. Restart the bot.",
                parse_mode="HTML", reply_markup=builder.as_markup(),
            )

    # Scan Now
    elif action == "scan":
        from bot.scanner import scan_symbol, _market_open_now, _broadcast_market_closed, _broadcast_signal
        from src.data_fetcher import get_live_price
        from src.risk_manager import calculate_trade
        from src.trading_hours import symbol_market_status
        market_open = _market_open_now()
        await callback.message.edit_text(
            "📡 <b>Scanning channel...</b>" if market_open else "🔴 <b>Market closed</b>\nSending status only.",
            parse_mode="HTML",
        )
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
        loop  = asyncio.get_running_loop()
        fired = 0
        checked = 0
        holds: list[str] = []
        errors: list[str] = []
        for symbol in watchlist:
            try:
                checked += 1
                symbol_open, _ = symbol_market_status(symbol)
                if not symbol_open:
                    await _broadcast_market_closed(callback.bot, symbol, loop)
                    fired += 1
                    continue

                result = await scan_symbol(symbol)
                if not result:
                    errors.append(f"{symbol}: scan failed")
                    continue
                if result["signal"].direction not in ("BUY", "SELL"):
                    reason = result["signal"].reason or "No clean setup"
                    holds.append(f"{symbol}: {reason}")
                    continue
                signal = result["signal"]
                trade  = result["trade"]
                atr    = result.get("atr", 0)

                live_price = None
                try:
                    live_price = await loop.run_in_executor(None, lambda s=symbol: get_live_price(s))
                except Exception:
                    pass
                if live_price and atr > 0:
                    trade = calculate_trade(signal.direction, live_price, atr, RISK_CFG, symbol=symbol)

                result["live_price"] = live_price
                result["trade"] = trade
                sent = await _broadcast_signal(callback.bot, symbol, result)
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
                    fired += 1
            except Exception as e:
                logger.error(f"Scan error {symbol}: {e}")
                errors.append(f"{symbol}: {e}")
        result_text = format_scan_summary(checked, fired, holds, errors)
        await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=_menu_markup())


# /watchlist
@router.message(Command("watchlist"))
async def cmd_watchlist(message: Message):
    if not await _check_owner(message): return
    async with AsyncSessionLocal() as session:
        watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
    if not watchlist:
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Add Symbol", callback_data="home:symbols")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(1)
        await message.answer(
            "📋 <b>Watchlist</b>\n\nEmpty. Add a symbol to start.",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )
        return
    await message.answer("🔍 <b>Scanning watchlist...</b>", parse_mode="HTML")
    results = []
    for symbol in watchlist:
        try:
            results.append(await _scan_symbol(symbol))
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e), "display_name": get_display_name(symbol), "signal": None})
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Refresh",    callback_data="home:watchlist")
    builder.button(text="➕ Add Symbol", callback_data="home:symbols")
    builder.button(text="🏠 Home",       callback_data="home:menu")
    builder.adjust(2, 1)
    await message.answer(format_watchlist_message(results), parse_mode="HTML", reply_markup=builder.as_markup())


# /add
@router.message(Command("add"))
async def cmd_add(message: Message):
    if not await _check_owner(message): return
    parts = message.text.split()
    if len(parts) < 2:
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Add Symbol", callback_data="home:symbols")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(1)
        await message.answer(
            "➕ <b>Add Symbol</b>\n\n"
            "Send a symbol like:\n"
            "<code>/add XAUUSD</code>\n\n"
            "You can also browse instruments with the button below.",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )
        return
    symbol = parts[1].upper()
    async with AsyncSessionLocal() as session:
        added = await add_symbol(session, cfg.OWNER_CHAT_ID, symbol)
    if added:
        await message.answer(f"✅ <b>{get_symbol_label(symbol)}</b>\n\nAdded to the watchlist.", parse_mode="HTML", reply_markup=_menu_markup())
    else:
        await message.answer(f"ℹ️ <b>{get_symbol_label(symbol)}</b>\n\nThis symbol is already in the watchlist.", parse_mode="HTML", reply_markup=_menu_markup())


# /remove
@router.message(Command("remove"))
async def cmd_remove(message: Message):
    if not await _check_owner(message): return
    parts = message.text.split()
    if len(parts) < 2:
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
        builder = InlineKeyboardBuilder()
        for s in watchlist:
            builder.button(text=f"❌ {get_symbol_label(s)}", callback_data=f"remove_sym:{s}")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(3)
        text = (
            "➖ <b>Remove Symbol</b>\n\nTap a symbol or send:\n<code>/remove XAUUSD</code>"
            if watchlist else
            "➖ <b>Remove Symbol</b>\n\nWatchlist is empty."
        )
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
        return
    symbol = parts[1].upper()
    async with AsyncSessionLocal() as session:
        removed = await remove_symbol(session, cfg.OWNER_CHAT_ID, symbol)
    if removed:
        await message.answer(f"✅ <b>{get_symbol_label(symbol)}</b>\n\nRemoved from the watchlist.", parse_mode="HTML", reply_markup=_menu_markup())
    else:
        await message.answer(f"ℹ️ <b>{get_symbol_label(symbol)}</b>\n\nThis symbol was not in your watchlist.", parse_mode="HTML", reply_markup=_menu_markup())


@router.callback_query(F.data.startswith("remove_sym:"))
async def cb_remove_symbol(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    symbol = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        removed = await remove_symbol(session, cfg.OWNER_CHAT_ID, symbol)
    if removed:
        await callback.answer(f"✅ {get_symbol_label(symbol)} removed.", show_alert=False)
        # Refresh the remove list
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
        builder = InlineKeyboardBuilder()
        for s in watchlist:
            builder.button(text=f"❌ {get_symbol_label(s)}", callback_data=f"remove_sym:{s}")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(3)
        text = (
            f"✅ <b>{get_symbol_label(symbol)}</b>\n\nRemoved from the watchlist.\n\n"
            + ("➖ <b>Remove Symbol</b>\n\nTap another symbol to remove it:" if watchlist else "Watchlist is now empty.")
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    else:
        await callback.answer("Not found.", show_alert=False)


async def _send_invite(bot, user_id: int) -> str:
    """Create a single-use invite link and send it directly to the user. Returns the link."""
    from bot.db.repositories.user_repo import mark_invited
    link_obj = await bot.create_chat_invite_link(
        cfg.BROADCAST_CHANNEL_ID, member_limit=2,
    )
    invite_link = link_obj.invite_link
    # Try to DM the link to the user
    try:
        await bot.send_message(
            user_id,
            "✅ <b>You've been approved!</b>\n\n"
            "👇 Tap the link below to join the private signals channel:\n"
            f"{invite_link}\n\n"
            "<i>Single-use link. Tap it once to join.</i>",
            parse_mode="HTML",
        )
    except Exception:
        pass  # user may not have started the bot; owner gets the link to share manually
    try:
        async with AsyncSessionLocal() as session:
            await mark_invited(session, user_id)
    except Exception:
        pass  # column may not exist yet — invite still works
    return invite_link


@router.callback_query(F.data.startswith("approve_user:"))
async def cb_approve_user(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    user_id = int(callback.data.split(":")[1])
    await callback.answer()

    from bot.db.models.user import User
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        db_user = result.scalar_one_or_none()
    display = (db_user.first_name or f"@{db_user.username}") if db_user else str(user_id)
    if db_user and db_user.username:
        display += f" (@{db_user.username})"

    builder = InlineKeyboardBuilder()
    builder.button(text="↩ Back", callback_data="home:users")
    builder.button(text="🏠 Home", callback_data="home:menu")
    builder.adjust(2)

    if not cfg.BROADCAST_CHANNEL_ID:
        await callback.message.edit_text(
            "❌ <b>Channel Not Configured</b>\n\n"
            "Set <code>BROADCAST_CHANNEL_ID</code> in <code>.env</code>.",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )
        return

    # If already invited, just re-send without creating another link
    already_invited = getattr(db_user, 'is_invited', False) if db_user else False

    try:
        invite_link = await _send_invite(callback.bot, user_id)
        status = "Re-sent" if already_invited else "Sent"
        await callback.message.edit_text(
            f"✅ <b>Invite {status} to {display}</b>\n\n"
            f"Link was sent directly to their Telegram.\n"
            f"If they haven't started the bot, share it manually:\n<code>{invite_link}</code>",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>Invite Failed</b>\n\n"
            f"Reason: <code>{e}</code>\n\n"
            "Check that the bot has <b>Invite Users</b> permission in the channel.",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )


# Kick user from channel
@router.callback_query(F.data.startswith("kick_user:"))
async def cb_kick_user(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    user_id = int(callback.data.split(":")[1])
    await callback.answer()

    from bot.db.models.user import User
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        db_user = result.scalar_one_or_none()
    display = (db_user.first_name or f"@{db_user.username}") if db_user else str(user_id)
    if db_user and db_user.username:
        display += f" (@{db_user.username})"

    builder = InlineKeyboardBuilder()
    builder.button(text="↩ Back", callback_data="home:users")
    builder.button(text="🏠 Home", callback_data="home:menu")
    builder.adjust(2)

    if not cfg.BROADCAST_CHANNEL_ID:
        await callback.message.edit_text(
            "❌ <b>Channel Not Configured</b>\n\n"
            "Set <code>BROADCAST_CHANNEL_ID</code> in <code>.env</code>.",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )
        return

    # Always reset DB flags regardless of whether Telegram ban succeeds
    from sqlalchemy import update as sa_update
    from bot.db.models.user import User as UserModel
    async with AsyncSessionLocal() as session:
        await session.execute(
            sa_update(UserModel).where(UserModel.id == user_id)
            .values(is_invited=False, is_premium=False, premium_until=None)
        )
        await session.commit()

    try:
        # Ban then immediately unban = kick (removes from channel, not permanent)
        await callback.bot.ban_chat_member(cfg.BROADCAST_CHANNEL_ID, user_id)
        await callback.bot.unban_chat_member(cfg.BROADCAST_CHANNEL_ID, user_id)
        await callback.message.edit_text(
            f"🚫 <b>{display}</b> has been removed from the channel.",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )
        logger.info(f"Kicked user {user_id} ({display}) from channel")
    except Exception as e:
        await callback.message.edit_text(
            f"⚠️ <b>{display}</b> removed from approved list.\n\n"
            f"Channel removal failed: <code>{e}</code>\n\n"
            "Check that the bot has <b>Ban Users</b> permission in the channel.",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )


# Delete user from bot list
@router.callback_query(F.data.startswith("delete_user:"))
async def cb_delete_user(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    user_id = int(callback.data.split(":")[1])
    await callback.answer()

    from bot.db.models.user import User
    from bot.db.repositories.user_repo import delete_user
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        db_user = result.scalar_one_or_none()
    display = (db_user.first_name or f"@{db_user.username}") if db_user else str(user_id)

    async with AsyncSessionLocal() as session:
        deleted = await delete_user(session, user_id)

    builder = InlineKeyboardBuilder()
    builder.button(text="↩ Back", callback_data="home:users")
    builder.button(text="🏠 Home", callback_data="home:menu")
    builder.adjust(2)

    if deleted:
        await callback.message.edit_text(
            f"🗑️ <b>{display}</b> removed from the bot list.\n\n"
            "They can be re-invited once they message the bot again.",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )
    else:
        await callback.message.edit_text(
            "❌ <b>User Not Found</b>\n\nThey may have already been deleted.",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )


# /signal
@router.message(Command("signal"))
async def cmd_signal(message: Message):
    if not await _check_owner(message): return
    parts = message.text.split()
    if len(parts) < 2:
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
        builder = InlineKeyboardBuilder()
        _symbol_buttons(builder, watchlist, "scan_sym")
        builder.button(text="🔎 Instruments", callback_data="home:symbols")
        builder.button(text="🏠 Home",       callback_data="home:menu")
        builder.adjust(3)
        await message.answer(
            "🔔 <b>Check Signal</b>\n\n" + ("Choose a symbol:" if watchlist else "Watchlist is empty. Add a symbol first."),
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )
        return
    symbol = parts[1].upper()
    msg    = await message.answer(
        f"🔍 <b>Scanning {symbol}...</b>",
        parse_mode="HTML",
    )
    try:
        symbol_open, _ = symbol_market_status(symbol)
        if not symbol_open:
            await _send_closed_symbol_chart(message, symbol, edit_message=msg)
            return

        r    = await _scan_symbol(symbol)
        loop = asyncio.get_running_loop()

        if r["signal"].direction in ("BUY", "SELL"):
            live_price = None
            try:
                live_price = await loop.run_in_executor(None, lambda s=symbol: get_live_price(s))
            except Exception:
                pass
            trade = r["trade"]
            if live_price and r["atr"] > 0:
                trade = calculate_trade(r["signal"].direction, live_price, r["atr"], RISK_CFG, symbol=symbol)
            text = format_signal_message(
                r["display_name"], r["signal"], trade,
                symbol=r["symbol"], live_price=live_price,
                market_pressure=r.get("market_pressure"),
            )
        else:
            text = format_hold_message(r["display_name"], r["signal"], symbol=r["symbol"])

        try:
            buf = await loop.run_in_executor(None, lambda s=symbol: generate_chart(s))
            from aiogram.types import BufferedInputFile
            await msg.delete()
            await message.answer_photo(
                BufferedInputFile(buf.read(), filename=f"{symbol}.png"),
                caption=text,
                parse_mode="HTML",
                reply_markup=_menu_markup(),
            )
        except Exception:
            await msg.edit_text(text, parse_mode="HTML", reply_markup=_menu_markup())
    except Exception as e:
        await msg.edit_text(f"❌ <b>Scan Failed</b>\n\nSymbol: <b>{symbol}</b>\nReason: <code>{e}</code>", parse_mode="HTML", reply_markup=_menu_markup())


@router.callback_query(F.data.startswith("scan_sym:"))
async def cb_scan_symbol(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    symbol = callback.data.split(":")[1]
    await callback.answer()
    await callback.message.edit_text(
        f"🔍 <b>Scanning {symbol}...</b>",
        parse_mode="HTML",
    )
    try:
        symbol_open, _ = symbol_market_status(symbol)
        if not symbol_open:
            await _send_closed_symbol_chart(callback.message, symbol, edit_message=callback.message)
            return

        r    = await _scan_symbol(symbol)
        loop = asyncio.get_running_loop()

        if r["signal"].direction in ("BUY", "SELL"):
            live_price = None
            try:
                live_price = await loop.run_in_executor(None, lambda s=symbol: get_live_price(s))
            except Exception:
                pass
            trade = r["trade"]
            if live_price and r["atr"] > 0:
                trade = calculate_trade(r["signal"].direction, live_price, r["atr"], RISK_CFG, symbol=symbol)
            text = format_signal_message(
                r["display_name"], r["signal"], trade,
                symbol=r["symbol"], live_price=live_price,
                market_pressure=r.get("market_pressure"),
            )
        else:
            text = format_hold_message(r["display_name"], r["signal"], symbol=r["symbol"])

        try:
            buf = await loop.run_in_executor(None, lambda s=symbol: generate_chart(s))
            from aiogram.types import BufferedInputFile
            await callback.message.delete()
            await callback.message.answer_photo(
                BufferedInputFile(buf.read(), filename=f"{symbol}.png"),
                caption=text,
                parse_mode="HTML",
                reply_markup=_menu_markup(),
            )
        except Exception:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_menu_markup())
    except Exception as e:
        await callback.message.edit_text(f"❌ <b>Scan Failed</b>\n\nSymbol: <b>{symbol}</b>\nReason: <code>{e}</code>", parse_mode="HTML", reply_markup=_menu_markup())


# /market
@router.message(Command("market"))
async def cmd_market(message: Message):
    if not await _check_owner(message): return
    async with AsyncSessionLocal() as session:
        watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
    builder = InlineKeyboardBuilder()
    if watchlist:
        _symbol_buttons(builder, watchlist, "news_sym")
    else:
        builder.button(text="➕ Add Symbol", callback_data="home:symbols")
    builder.button(text="🏠 Home", callback_data="home:menu")
    builder.adjust(3)
    text = (
        "📰 <b>Market News</b>\n\nChoose a symbol:"
        if watchlist else
        "📰 <b>Market News</b>\n\nWatchlist is empty. Add a symbol first."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("news_sym:"))
async def cb_news_symbol(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    symbol = callback.data.split(":")[1]
    await callback.answer()
    await callback.message.edit_text(f"📰 <b>Loading {symbol} news...</b>", parse_mode="HTML")
    try:
        loop = asyncio.get_running_loop()
        news = await loop.run_in_executor(None, lambda: get_news(symbol))
        builder = InlineKeyboardBuilder()
        builder.button(text="↩ Back", callback_data="home:market")
        builder.button(text="🏠 Home", callback_data="home:menu")
        builder.adjust(2)
        await callback.message.edit_text(
            format_news_message(symbol, news),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=builder.as_markup(),
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>News Unavailable</b>\n\nSymbol: <b>{symbol}</b>\nReason: <code>{e}</code>",
            parse_mode="HTML",
            reply_markup=_menu_markup(),
        )


@router.callback_query(F.data.startswith("calendar:"))
async def cb_calendar(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    mode       = callback.data.split(":")[1]
    today_only = mode == "today"
    await callback.answer("Fetching...")
    await callback.message.edit_text("📅 <b>Loading calendar...</b>", parse_mode="HTML")
    loop   = asyncio.get_running_loop()
    events = await loop.run_in_executor(None, lambda: get_calendar(today_only=today_only))

    builder = InlineKeyboardBuilder()
    if today_only:
        builder.button(text="📅 This Week", callback_data="calendar:week")
    else:
        builder.button(text="📅 Today",     callback_data="calendar:today")
    builder.button(text="↩ Back", callback_data="home:calendar")
    builder.button(text="🏠 Home", callback_data="home:menu")
    builder.adjust(3)
    await callback.message.edit_text(
        format_calendar_message(events, today_only=today_only),
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


# /calendar
@router.message(Command("calendar"))
async def cmd_calendar(message: Message):
    if not await _check_owner(message): return
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Today",     callback_data="calendar:today")
    builder.button(text="📅 This Week", callback_data="calendar:week")
    builder.button(text="🏠 Home",      callback_data="home:menu")
    builder.adjust(2, 1)
    await message.answer(
        "📅 <b>Economic Calendar</b>\n\nChoose which events to show:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


# /hours
@router.message(Command("hours"))
async def cmd_hours(message: Message):
    if not await _check_owner(message): return
    await message.answer(get_hours_message(), parse_mode="HTML", reply_markup=_menu_markup())


# /history
@router.message(Command("history"))
async def cmd_history(message: Message):
    if not await _check_owner(message): return
    parts = message.text.split()
    limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
    limit = min(limit, 200)
    async with AsyncSessionLocal() as session:
        signals = await get_recent_signals(session, limit=limit)
    builder = InlineKeyboardBuilder()
    if limit <= 20:
        builder.button(text="Show 50",  callback_data="home:history:50")
    if limit <= 50:
        builder.button(text="Show 100", callback_data="home:history:100")
    builder.button(text="🗑 Clear History", callback_data="home:history:clear")
    builder.button(text="🏠 Home",          callback_data="home:menu")
    builder.adjust(2)
    await message.answer(format_history_message(signals, limit=limit), parse_mode="HTML", reply_markup=builder.as_markup())


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not await _check_owner(message): return
    async with AsyncSessionLocal() as session:
        stats = await get_signal_stats(session, limit=100)
    builder = InlineKeyboardBuilder()
    builder.button(text="📜 History", callback_data="home:history")
    builder.button(text="🏠 Home", callback_data="home:menu")
    builder.adjust(2)
    await message.answer(format_stats_message(stats), parse_mode="HTML", reply_markup=builder.as_markup())




# /scan
@router.message(Command("scan"))
async def cmd_scan(message: Message):
    if not await _check_owner(message): return
    from bot.scanner import scan_symbol, _market_open_now, _broadcast_market_closed, _broadcast_signal
    from src.risk_manager import calculate_trade
    from src.data_fetcher import get_live_price
    from src.trading_hours import symbol_market_status
    market_open = _market_open_now()
    async with AsyncSessionLocal() as session:
        watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
    if not watchlist:
        await message.answer("Watchlist is empty.", reply_markup=_menu_markup())
        return
    status = "checking for channel alerts" if market_open else "market closed, sending status updates"
    msg = await message.answer(f"🔍 Scanning <b>{len(watchlist)}</b> symbol(s), {status}...", parse_mode="HTML")
    loop  = asyncio.get_running_loop()
    fired = 0
    checked = 0
    holds: list[str] = []
    errors: list[str] = []
    for symbol in watchlist:
        try:
            checked += 1
            symbol_open, _ = symbol_market_status(symbol)
            if not symbol_open:
                await _broadcast_market_closed(message.bot, symbol, loop)
                fired += 1
                continue

            result = await scan_symbol(symbol)
            if not result:
                errors.append(f"{symbol}: scan failed")
                continue
            if result["signal"].direction not in ("BUY", "SELL"):
                reason = result["signal"].reason or "No clean setup"
                holds.append(f"{symbol}: {reason}")
                continue
            signal = result["signal"]
            trade  = result["trade"]
            atr    = result.get("atr", 0)

            live_price = None
            try:
                live_price = await loop.run_in_executor(None, lambda s=symbol: get_live_price(s))
            except Exception:
                pass
            if live_price and atr > 0:
                trade = calculate_trade(signal.direction, live_price, atr, RISK_CFG, symbol=symbol)

            result["live_price"] = live_price
            result["trade"] = trade
            sent = await _broadcast_signal(message.bot, symbol, result)
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
                fired += 1
        except Exception as e:
            logger.error(f"Scan error {symbol}: {e}")
            errors.append(f"{symbol}: {e}")
    result_text = format_scan_summary(checked, fired, holds, errors)
    await msg.edit_text(result_text, parse_mode="HTML", reply_markup=_menu_markup())


# /chart
@router.message(Command("chart"))
async def cmd_chart(message: Message):
    if not await _check_owner(message): return
    parts = message.text.split()
    if len(parts) < 2:
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
        builder = InlineKeyboardBuilder()
        _symbol_buttons(builder, watchlist, "chart_sym")
        builder.button(text="🔎 Instruments", callback_data="home:symbols")
        builder.button(text="🏠 Home",       callback_data="home:menu")
        builder.adjust(3)
        await message.answer(
            "📊 <b>Chart</b>\n\nChoose a symbol:",
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )
        return
    symbol = parts[1].upper()
    msg = await message.answer(f"📊 <b>Generating {symbol} chart...</b>", parse_mode="HTML")
    try:
        loop  = asyncio.get_running_loop()
        buf   = await loop.run_in_executor(None, lambda: generate_chart(symbol))
        from aiogram.types import BufferedInputFile
        await msg.delete()
        await message.answer_photo(
            BufferedInputFile(buf.read(), filename=f"{symbol}_1h.png"),
            caption=f"📊 <b>{get_symbol_label(symbol)}</b>\nTimeframe: <b>1H</b>",
            parse_mode="HTML",
            reply_markup=_menu_markup(),
        )
    except Exception as e:
        await msg.edit_text(f"❌ <b>Chart Failed</b>\n\nSymbol: <b>{symbol}</b>\nReason: <code>{e}</code>", parse_mode="HTML", reply_markup=_menu_markup())


@router.callback_query(F.data.startswith("chart_sym:"))
async def cb_chart_symbol(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    symbol = callback.data.split(":")[1]
    await callback.answer()
    await callback.message.edit_text(f"📊 <b>Generating {symbol} chart...</b>", parse_mode="HTML")
    try:
        loop  = asyncio.get_running_loop()
        buf   = await loop.run_in_executor(None, lambda: generate_chart(symbol))
        from aiogram.types import BufferedInputFile
        await callback.message.delete()
        await callback.message.answer_photo(
            BufferedInputFile(buf.read(), filename=f"{symbol}_1h.png"),
            caption=f"📊 <b>{get_symbol_label(symbol)}</b>\nTimeframe: <b>1H</b>",
            parse_mode="HTML",
            reply_markup=_menu_markup(),
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ <b>Chart Failed</b>\n\nSymbol: <b>{symbol}</b>\nReason: <code>{e}</code>", parse_mode="HTML", reply_markup=_menu_markup())


# Instrument browser
@router.message(Command("symbols"))
async def cmd_symbols(message: Message):
    if not await _check_owner(message): return
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(text=category, callback_data=f"cat:{category}")
    builder.button(text="🏠 Home", callback_data="home:menu")
    builder.adjust(2)
    await message.answer("🔎 <b>Instruments</b>\n\nChoose a market category:", parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    category = callback.data[4:]
    symbols  = CATEGORIES.get(category, [])
    async with AsyncSessionLocal() as session:
        watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
    builder = InlineKeyboardBuilder()
    for s in symbols:
        if s in watchlist:
            builder.button(text=f"✅ {get_symbol_label(s)}", callback_data=f"noop:{s}")
        else:
            builder.button(text=f"➕ {get_symbol_label(s)}", callback_data=f"quickadd:{s}")
    builder.button(text="↩ Back", callback_data="home:symbols")
    builder.adjust(1)
    await callback.message.edit_text(
        f"🔎 <b>{category}</b>\n\nTap a symbol to add it.\n✅ Already in watchlist.",
        parse_mode="HTML", reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("noop:"))
async def cb_noop(callback: CallbackQuery):
    await callback.answer("Already in watchlist.", show_alert=False)


@router.callback_query(F.data.startswith("quickadd:"))
async def cb_quickadd(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    symbol = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        added = await add_symbol(session, cfg.OWNER_CHAT_ID, symbol)
    if added:
        await callback.answer(f"✅ {symbol} added.", show_alert=False)
        # Refresh the category view to show ✅
        category = None
        for cat, syms in CATEGORIES.items():
            if symbol in syms:
                category = cat
                break
        if category:
            async with AsyncSessionLocal() as session:
                watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
            builder = InlineKeyboardBuilder()
            for s in CATEGORIES[category]:
                if s in watchlist:
                    builder.button(text=f"✅ {get_symbol_label(s)}", callback_data=f"noop:{s}")
                else:
                    builder.button(text=f"➕ {get_symbol_label(s)}", callback_data=f"quickadd:{s}")
            builder.button(text="↩ Back", callback_data="home:symbols")
            builder.adjust(1)
            await callback.message.edit_text(
                f"🔎 <b>{category}</b>\n\nTap a symbol to add it.\n✅ Already in watchlist.",
                parse_mode="HTML", reply_markup=builder.as_markup(),
            )
    else:
        await callback.answer("Already in watchlist.", show_alert=False)


# Watchlist refresh (button on watchlist card)
@router.callback_query(F.data == "refresh:watchlist")
async def cb_refresh_watchlist(callback: CallbackQuery):
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    await callback.answer("Refreshing...")
    await callback.message.edit_text("🔍 <b>Refreshing watchlist...</b>", parse_mode="HTML")
    async with AsyncSessionLocal() as session:
        watchlist = await get_watchlist(session, cfg.OWNER_CHAT_ID)
    results = []
    for symbol in watchlist:
        try:
            results.append(await _scan_symbol(symbol))
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e), "display_name": get_display_name(symbol), "signal": None})
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Refresh", callback_data="home:watchlist")
    builder.button(text="➕ Add",     callback_data="home:symbols")
    builder.button(text="🏠 Home",    callback_data="home:menu")
    builder.adjust(2, 1)
    await callback.message.edit_text(format_watchlist_message(results), parse_mode="HTML", reply_markup=builder.as_markup())


# User subscription flow
@router.callback_query(F.data == "sub:plans")
async def cb_sub_plans(callback: CallbackQuery):
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="1 Month  $99",   callback_data="buy_plan:monthly")
    builder.button(text="3 Months  $199", callback_data="buy_plan:quarterly")
    builder.button(text="Lifetime  $299", callback_data="buy_plan:lifetime")
    builder.adjust(1)
    await callback.message.edit_text(
        "⭐️ <b>CFD Smart Signals</b>\n\n"
        "Private CFD signal channel.\n"
        "Signals include Entry, SL, TP1, TP2 and TP3.\n\n"
        "1 Month: <b>$99</b>\n"
        "3 Months: <b>$199</b>\n"
        "Lifetime: <b>$299</b>\n\n"
        "Pay with Telegram Stars. Access is instant.",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("buy_plan:"))
async def cb_buy_plan(callback: CallbackQuery):
    plan_id = callback.data.split(":")[1]
    plan    = _PLANS.get(plan_id)
    if not plan:
        await callback.answer("Invalid plan.", show_alert=True)
        return
    await callback.answer()
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"CFD Smart Signals {plan['label']}",
        description="Live CFD trading signals with Entry, Stop Loss and 3 Take Profit levels, delivered to a private VIP channel.",
        payload=f"plan:{plan_id}",
        currency="XTR",
        prices=[LabeledPrice(label=plan["label"], amount=plan["stars"])],
    )


@router.pre_checkout_query()
async def handle_pre_checkout(query: PreCheckoutQuery):
    plan_id = query.invoice_payload.removeprefix("plan:")
    if query.invoice_payload.startswith("plan:") and plan_id in _PLANS:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Unknown plan.")


@router.message(F.successful_payment)
async def handle_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("plan:"):
        return

    plan_id = payload.split(":")[1]
    plan    = _PLANS.get(plan_id)
    if not plan:
        await message.answer("❌ Unknown plan. Please contact support.")
        return

    user_id = message.from_user.id
    until   = datetime(2099, 12, 31, tzinfo=timezone.utc) if plan["days"] == 0 \
              else datetime.now(timezone.utc) + timedelta(days=plan["days"])

    # Ensure user row exists before granting premium
    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, user_id, message.from_user.username, message.from_user.first_name)
    async with AsyncSessionLocal() as session:
        await grant_premium_until(session, user_id, until)

    expiry_str = "Lifetime" if plan["days"] == 0 else until.strftime("%d %b %Y")

    # Also mark as invited so join request handler has a fallback approval gate
    try:
        async with AsyncSessionLocal() as session:
            from bot.db.repositories.user_repo import mark_invited
            await mark_invited(session, user_id)
    except Exception:
        pass

    invite_link = None
    if cfg.BROADCAST_CHANNEL_ID:
        try:
            link_obj = await message.bot.create_chat_invite_link(
                cfg.BROADCAST_CHANNEL_ID, member_limit=1,
            )
            invite_link = link_obj.invite_link
        except Exception as e:
            logger.warning(f"Could not create invite link: {e}")

    text = (
        f"✅ <b>Payment Received</b>\n\n"
        f"Plan: <b>{plan['label']}</b>\n"
        f"Valid until: <b>{expiry_str}</b>\n\n"
    )
    if invite_link:
        text += f"Join the private channel:\n{invite_link}\n\n<i>Single-use link.</i>"
    else:
        text += "Access is active. Channel invite will be sent shortly."

    await message.answer(text, parse_mode="HTML")

    name     = message.from_user.first_name or message.from_user.username or str(user_id)
    username = f" @{message.from_user.username}" if message.from_user.username else ""
    stars    = message.successful_payment.total_amount
    try:
        await message.bot.send_message(
            cfg.OWNER_CHAT_ID,
            f"⭐ <b>New Subscriber</b>\n\n"
            f"👤 <b>{name}</b>{username}\n"
            f"🆔 <code>{user_id}</code>\n"
            f"Plan: <b>{plan['label']}</b>  ({stars} Stars)\n"
            f"Valid until: <b>{expiry_str}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    logger.info(f"New subscriber: user_id={user_id} plan={plan_id} until={expiry_str}")


# Channel join request handler
@router.chat_join_request()
async def handle_join_request(request: ChatJoinRequest):
    """Auto-approve paid/invited users. Decline everyone else and send subscribe link."""
    user_id = request.from_user.id

    from bot.db.models.user import User
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        db_user = result.scalar_one_or_none()

    is_approved = (
        db_user is not None and (
            getattr(db_user, 'is_invited', False) or
            (db_user.is_premium and (
                db_user.premium_until is None or
                db_user.premium_until > datetime.now(timezone.utc)
            ))
        )
    )

    if is_approved:
        try:
            await request.approve()
            logger.info(f"Auto-approved join request: {user_id}")
        except Exception as e:
            logger.error(f"Could not approve join request {user_id}: {e}")
    else:
        try:
            await request.decline()
        except Exception as e:
            logger.error(f"Could not decline join request {user_id}: {e}")

        # Register them and send subscribe prompt
        async with AsyncSessionLocal() as session:
            await get_or_create_user(
                session, user_id,
                request.from_user.username,
                request.from_user.first_name,
            )
        name = request.from_user.first_name or request.from_user.username or "there"
        builder = InlineKeyboardBuilder()
        builder.button(text="⭐ Subscribe Now", callback_data="sub:plans")
        try:
            await request.bot.send_message(
                user_id,
                f"👋 <b>Hi {name}</b>\n\n"
                "This is a private signals channel.\n\n"
                "Subscribe to get access to alerts with Entry, SL, TP1, TP2 and TP3.",
                parse_mode="HTML",
                reply_markup=builder.as_markup(),
            )
        except Exception:
            pass  # user hasn't started the bot yet — they'll see it when they do
        logger.info(f"Declined join request and sent subscribe prompt: {user_id}")


# Catch-all for non-owner users
@router.message()
async def catch_all_user(message: Message):
    """Show subscribe prompt to any non-owner user who messages the bot."""
    if _is_owner(message.from_user.id):
        return  # owner's unhandled messages — ignore silently

    try:
        async with AsyncSessionLocal() as session:
            await get_or_create_user(
                session,
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name,
            )
    except Exception as exc:
        logger.warning(f"Could not register user {message.from_user.id}: {exc}")

    name = message.from_user.first_name or message.from_user.username or "there"
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Subscribe Now", callback_data="sub:plans")
    await message.answer(
        f"👋 <b>Welcome, {name}</b>\n\n"
        "📡 <b>CFD Smart Signals</b>\n"
        "Private alerts for Forex, Gold and Indices.\n\n"
        "Each alert includes Entry, SL, TP1, TP2 and TP3.\n"
        "Subscribe to get channel access.",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )
