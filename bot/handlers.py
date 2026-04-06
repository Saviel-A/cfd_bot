"""All Telegram command handlers — fully multi-user, DB-backed."""

import asyncio
import logging

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.session import AsyncSessionLocal
from bot.db.repositories.user_repo import get_or_create_user, is_premium_or_owner
from bot.db.repositories.settings_repo import get_settings, update_settings
from bot.db.repositories.watchlist_repo import get_watchlist, add_symbol, remove_symbol
from bot.db.repositories.signal_repo import get_recent_signals, get_performance_stats
from bot.formatter import (
    format_signal_message, format_hold_message, format_watchlist_message,
    format_settings_message, format_history_message, format_performance_message,
)
from src.instruments import load_instrument_cfg, get_display_name, get_ticker_for_symbol, CATEGORIES
from src.data_fetcher import fetch_ohlcv
from src.indicators import compute_all
from src.signal_engine import generate_signal
from src.risk_manager import calculate_trade
from src.news import get_news, format_news_message
from src.trading_hours import get_hours_message
from src.calendar import get_calendar, format_calendar_message
from bot.config import cfg

logger = logging.getLogger(__name__)
router = Router()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _ensure_user(message: Message):
    async with AsyncSessionLocal() as session:
        return await get_or_create_user(
            session,
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
        )


def _is_owner(user_id: int) -> bool:
    return user_id == cfg.OWNER_CHAT_ID


async def _check_access(message: Message) -> bool:
    if _is_owner(message.from_user.id):
        return True
    await message.answer(
        "📡 <b>CFD Smart Signals</b>\n\n"
        "Subscribe to our channel to receive trading signals.",
        parse_mode="HTML",
    )
    return False


async def _scan_symbol_for_user(symbol: str, user_id: int) -> dict:
    async with AsyncSessionLocal() as session:
        settings = await get_settings(session, user_id)

    cfg_inst = load_instrument_cfg(symbol)
    ticker   = cfg_inst.get("ticker", symbol)
    tf       = str(settings.timeframe)
    htf      = str(settings.htf_timeframe)

    loop = asyncio.get_running_loop()
    df     = await loop.run_in_executor(None, lambda: fetch_ohlcv(ticker, timeframe=tf, lookback=200))
    df_htf = await loop.run_in_executor(None, lambda: fetch_ohlcv(ticker, timeframe=htf, lookback=300))
    df = compute_all(df, cfg_inst)

    signal = generate_signal(df, {
        "signals": {
            "min_confluence": int(settings.min_confluence),
            "indicators": {"ema_cross": True, "rsi": True, "macd": True, "bollinger": True, "atr": True},
        }
    }, cfg_inst, df_htf=df_htf)

    atr = float(df.iloc[-1].get("atr", 0) or 0)
    trade = None
    if signal.direction in ("BUY", "SELL"):
        trade = calculate_trade(signal.direction, signal.current_price, atr, {
            "account_balance":   float(settings.account_balance),
            "risk_percent":      float(settings.risk_percent),
            "sl_atr_multiplier": float(settings.sl_atr_multiplier),
            "rr1": float(settings.rr1),
            "rr2": float(settings.rr2),
            "rr3": float(settings.rr3),
        })
    return {"symbol": symbol, "signal": signal, "trade": trade, "display_name": get_display_name(symbol)}


def _watchlist_symbol_buttons(watchlist: list, callback_prefix: str, cols: int = 3) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for s in watchlist:
        builder.button(text=s, callback_data=f"{callback_prefix}:{s}")
    builder.adjust(cols)
    return builder


# ── /start & /help ────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    await _ensure_user(message)
    is_owner = message.from_user.id == cfg.OWNER_CHAT_ID

    if not _is_owner(message.from_user.id):
        await message.answer(
            "📡 <b>CFD Smart Signals</b>\n\n"
            "Subscribe to our channel to receive trading signals.",
            parse_mode="HTML",
        )
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Watchlist",    callback_data="home:watchlist")
    builder.button(text="📊 Signal",       callback_data="home:signal")
    builder.button(text="🔎 Instruments",  callback_data="home:symbols")
    builder.button(text="📰 News",         callback_data="home:news")
    builder.button(text="📜 History",      callback_data="home:history")
    builder.button(text="⚙️ Settings",     callback_data="home:settings")
    builder.button(text="🕐 Market Hours",     callback_data="home:hours")
    builder.button(text="📅 Calendar",          callback_data="home:calendar")
    builder.adjust(2)

    await message.answer(
        "📡 <b>CFD Smart Signal Bot</b>\n\n"
        "Multi-timeframe signals with automatic SL and 3 TP levels.",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("home:"))
async def cb_home(callback: CallbackQuery):
    action  = callback.data.split(":")[1]
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    bot     = callback.bot
    await callback.answer()

    async def send(text, **kw):
        await bot.send_message(chat_id, text, **kw)

    if not _is_owner(user_id):
        await send("📡 <b>CFD Smart Signals</b>\n\nSubscribe to our channel to receive trading signals.", parse_mode="HTML")
        return

    if action == "watchlist":
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, user_id)
        if not watchlist:
            builder = InlineKeyboardBuilder()
            builder.button(text="🔎 Browse Instruments", callback_data="home:symbols")
            await send("📋 <b>Your watchlist is empty.</b>", parse_mode="HTML", reply_markup=builder.as_markup())
            return
        await send("🔍 Scanning your watchlist...", parse_mode="HTML")
        results = []
        for symbol in watchlist:
            try:
                r = await _scan_symbol_for_user(symbol, user_id)
                results.append(r)
            except Exception as e:
                results.append({"symbol": symbol, "error": str(e), "display_name": get_display_name(symbol), "signal": None})
        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Refresh", callback_data="refresh:watchlist")
        builder.button(text="➕ Add Symbol", callback_data="home:symbols")
        builder.adjust(2)
        await send(format_watchlist_message(results), parse_mode="HTML", reply_markup=builder.as_markup())

    elif action == "history":
        async with AsyncSessionLocal() as session:
            signals = await get_recent_signals(session, user_id, limit=10)
        builder = InlineKeyboardBuilder()
        builder.button(text="🏆 Performance", callback_data="home:performance")
        await send(format_history_message(signals), parse_mode="HTML", reply_markup=builder.as_markup())

    elif action == "performance":
        async with AsyncSessionLocal() as session:
            stats = await get_performance_stats(session, user_id)
        builder = InlineKeyboardBuilder()
        builder.button(text="📜 Signal History", callback_data="home:history")
        await send(format_performance_message(stats), parse_mode="HTML", reply_markup=builder.as_markup())

    elif action == "settings":
        async with AsyncSessionLocal() as session:
            s        = await get_settings(session, user_id)
            watchlist = await get_watchlist(session, user_id)
        builder = InlineKeyboardBuilder()
        builder.button(text="🔔 Alerts",     callback_data="open_setting:alerts")
        builder.button(text="⏱ Timeframe",  callback_data="open_setting:timeframe")
        builder.button(text="🎯 Confluence", callback_data="open_setting:confluence")
        builder.button(text="💰 Balance",    callback_data="open_setting:balance")
        builder.button(text="⚡ Risk",       callback_data="open_setting:risk")
        builder.adjust(2)
        await send(format_settings_message(s, len(watchlist)), parse_mode="HTML", reply_markup=builder.as_markup())

    elif action == "signal":
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, user_id)
        builder = InlineKeyboardBuilder()
        if watchlist:
            for s in watchlist:
                builder.button(text=s, callback_data=f"scan_sym:{s}")
        builder.button(text="🔎 Browse Instruments", callback_data="home:symbols")
        builder.adjust(3)
        await send(
            "📊 <b>Get a Signal</b>\n\n" + ("Choose from your watchlist:" if watchlist else "Your watchlist is empty. Browse instruments:"),
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    elif action == "news":
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, user_id)
        builder = InlineKeyboardBuilder()
        if watchlist:
            for s in watchlist:
                builder.button(text=s, callback_data=f"news_sym:{s}")
        builder.button(text="🔎 Browse Instruments", callback_data="home:symbols")
        builder.adjust(3)
        await send(
            "📰 <b>Latest News</b>\n\n" + ("Choose a symbol:" if watchlist else "Your watchlist is empty:"),
            parse_mode="HTML", reply_markup=builder.as_markup(),
        )

    elif action == "symbols":
        builder = InlineKeyboardBuilder()
        for category in CATEGORIES:
            builder.button(text=category, callback_data=f"cat:{category}")
        builder.adjust(2)
        await send("🔎 <b>Instruments</b>\n\nSelect a category:", parse_mode="HTML", reply_markup=builder.as_markup())

    elif action == "hours":
        await send(get_hours_message(), parse_mode="HTML")

    elif action == "calendar":
        await send("📅 Fetching calendar...", parse_mode="HTML")
        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(None, lambda: get_calendar(today_only=True))
        builder = InlineKeyboardBuilder()
        builder.button(text="📅 Full Week", callback_data="calendar:week")
        await callback.message.answer(
            format_calendar_message(events, today_only=True),
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )



@router.message(Command("help"))
async def cmd_help(message: Message):
    await cmd_start(message)


# ── /calendar ────────────────────────────────────────────────────────────────

@router.message(Command("calendar"))
async def cmd_calendar(message: Message):
    await _ensure_user(message)

    parts = message.text.split()
    week  = len(parts) > 1 and parts[1].lower() == "week"

    await message.answer("📅 Fetching calendar...", parse_mode="HTML")
    loop   = asyncio.get_running_loop()
    events = await loop.run_in_executor(None, lambda: get_calendar(today_only=not week))

    builder = InlineKeyboardBuilder()
    if not week:
        builder.button(text="📅 Full Week", callback_data="calendar:week")
    else:
        builder.button(text="📅 Today Only", callback_data="calendar:today")

    await message.answer(
        format_calendar_message(events, today_only=not week),
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("calendar:"))
async def cb_calendar(callback: CallbackQuery):
    mode     = callback.data.split(":")[1]
    today_only = mode == "today"
    await callback.answer("Fetching...")
    loop   = asyncio.get_running_loop()
    events = await loop.run_in_executor(None, lambda: get_calendar(today_only=today_only))

    builder = InlineKeyboardBuilder()
    if today_only:
        builder.button(text="📅 Full Week", callback_data="calendar:week")
    else:
        builder.button(text="📅 Today Only", callback_data="calendar:today")

    await callback.message.edit_text(
        format_calendar_message(events, today_only=today_only),
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


# ── /hours ───────────────────────────────────────────────────────────────────

@router.message(Command("hours"))
async def cmd_hours(message: Message):
    await _ensure_user(message)
    await message.answer(get_hours_message(), parse_mode="HTML")


# ── /watchlist ────────────────────────────────────────────────────────────────

@router.message(Command("watchlist"))
async def cmd_watchlist(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    async with AsyncSessionLocal() as session:
        watchlist = await get_watchlist(session, message.from_user.id)

    if not watchlist:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔎 Browse Instruments", callback_data="home:symbols")
        await message.answer(
            "📋 <b>Your watchlist is empty.</b>\n\nBrowse instruments to get started.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    await message.answer("🔍 Scanning your watchlist...", parse_mode="HTML")
    results = []
    for symbol in watchlist:
        try:
            r = await _scan_symbol_for_user(symbol, message.from_user.id)
            results.append(r)
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e), "display_name": get_display_name(symbol), "signal": None})

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Refresh", callback_data="refresh:watchlist")
    builder.button(text="➕ Add Symbol", callback_data="home:symbols")
    builder.adjust(2)

    await message.answer(format_watchlist_message(results), parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data == "refresh:watchlist")
async def cb_refresh_watchlist(callback: CallbackQuery):
    await callback.answer("Refreshing...")
    async with AsyncSessionLocal() as session:
        watchlist = await get_watchlist(session, callback.from_user.id)

    results = []
    for symbol in watchlist:
        try:
            r = await _scan_symbol_for_user(symbol, callback.from_user.id)
            results.append(r)
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e), "display_name": get_display_name(symbol), "signal": None})

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Refresh", callback_data="refresh:watchlist")
    builder.button(text="➕ Add Symbol", callback_data="home:symbols")
    builder.adjust(2)

    await callback.message.edit_text(format_watchlist_message(results), parse_mode="HTML", reply_markup=builder.as_markup())


# ── /add ──────────────────────────────────────────────────────────────────────

@router.message(Command("add"))
async def cmd_add(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    parts = message.text.split()
    if len(parts) < 2:
        builder = InlineKeyboardBuilder()
        for category in CATEGORIES:
            builder.button(text=category, callback_data=f"cat:{category}")
        builder.adjust(2)
        await message.answer(
            "➕ <b>Add a Symbol</b>\n\n"
            "Browse by category below, or type directly:\n"
            "<code>/add XAUUSD</code>",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    symbol = parts[1].upper()
    ticker = get_ticker_for_symbol(symbol)
    await message.answer(f"🔍 Checking <b>{symbol}</b>...", parse_mode="HTML")

    try:
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, lambda: fetch_ohlcv(ticker, timeframe="1h", lookback=10))
        if df.empty:
            raise ValueError("No data")
    except Exception:
        await message.answer(
            f"❌ <b>{symbol}</b> not found.\n\nCheck the symbol or browse /symbols.",
            parse_mode="HTML",
        )
        return

    async with AsyncSessionLocal() as session:
        added = await add_symbol(session, message.from_user.id, symbol)

    if added:
        builder = InlineKeyboardBuilder()
        builder.button(text="📋 View Watchlist", callback_data="refresh:watchlist")
        builder.button(text="➕ Add More", callback_data="home:symbols")
        builder.adjust(2)
        await message.answer(
            f"✅ <b>{get_display_name(symbol)}</b> added to your watchlist.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
    else:
        await message.answer(f"<b>{symbol}</b> is already in your watchlist.", parse_mode="HTML")


# ── /remove ───────────────────────────────────────────────────────────────────

@router.message(Command("remove"))
async def cmd_remove(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    parts = message.text.split()
    if len(parts) < 2:
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, message.from_user.id)
        if not watchlist:
            await message.answer("📋 Your watchlist is empty.", parse_mode="HTML")
            return
        builder = InlineKeyboardBuilder()
        for s in watchlist:
            builder.button(text=f"✕  {s}", callback_data=f"remove:{s}")
        builder.adjust(2)
        await message.answer(
            "➖ <b>Remove a Symbol</b>\n\nTap to remove:",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    symbol = parts[1].upper()
    async with AsyncSessionLocal() as session:
        removed = await remove_symbol(session, message.from_user.id, symbol)
    if removed:
        await message.answer(f"✅ <b>{symbol}</b> removed from your watchlist.", parse_mode="HTML")
    else:
        await message.answer(f"<b>{symbol}</b> is not in your watchlist.", parse_mode="HTML")


@router.callback_query(F.data.startswith("remove:"))
async def cb_remove(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        await remove_symbol(session, callback.from_user.id, symbol)
    await callback.message.edit_text(f"✅ <b>{symbol}</b> removed from your watchlist.", parse_mode="HTML")
    await callback.answer()


# ── /signal ───────────────────────────────────────────────────────────────────

@router.message(Command("signal"))
async def cmd_signal(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    parts = message.text.split()
    if len(parts) < 2:
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, message.from_user.id)

        builder = InlineKeyboardBuilder()
        if watchlist:
            for s in watchlist:
                builder.button(text=s, callback_data=f"scan_sym:{s}")
            builder.adjust(3)
        builder.button(text="🔎 Browse Instruments", callback_data="home:symbols")
        builder.adjust(3)

        await message.answer(
            "📊 <b>Get a Signal</b>\n\n"
            + ("Choose from your watchlist:" if watchlist else "Your watchlist is empty. Browse instruments:"),
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    await _do_signal_scan(message, parts[1].upper(), message.from_user.id)


@router.callback_query(F.data.startswith("scan_sym:"))
async def cb_scan_symbol(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    await callback.answer()
    await callback.message.answer(f"🔍 Scanning <b>{symbol}</b>...", parse_mode="HTML")
    try:
        r = await _scan_symbol_for_user(symbol, callback.from_user.id)
        if r["signal"].direction in ("BUY", "SELL"):
            text = format_signal_message(r["display_name"], r["signal"], r["trade"], symbol=r["symbol"])
        else:
            text = format_hold_message(r["display_name"], r["signal"], symbol=r["symbol"])
        await callback.message.answer(text, parse_mode="HTML")
    except Exception as e:
        await callback.message.answer(f"❌ Could not scan <b>{symbol}</b>: {e}", parse_mode="HTML")


async def _do_signal_scan(message: Message, symbol: str, user_id: int):
    await message.answer(f"🔍 Scanning <b>{symbol}</b>...", parse_mode="HTML")
    try:
        r = await _scan_symbol_for_user(symbol, user_id)
        if r["signal"].direction in ("BUY", "SELL"):
            text = format_signal_message(r["display_name"], r["signal"], r["trade"], symbol=r["symbol"])
        else:
            text = format_hold_message(r["display_name"], r["signal"], symbol=r["symbol"])
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Could not scan <b>{symbol}</b>: {e}", parse_mode="HTML")


# ── /news ─────────────────────────────────────────────────────────────────────

@router.message(Command("news"))
async def cmd_news(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    parts = message.text.split()
    if len(parts) < 2:
        async with AsyncSessionLocal() as session:
            watchlist = await get_watchlist(session, message.from_user.id)

        builder = InlineKeyboardBuilder()
        if watchlist:
            for s in watchlist:
                builder.button(text=s, callback_data=f"news_sym:{s}")
            builder.adjust(3)
        builder.button(text="🔎 Browse Instruments", callback_data="home:symbols")
        builder.adjust(3)

        await message.answer(
            "📰 <b>Latest News</b>\n\n"
            + ("Choose a symbol:" if watchlist else "Your watchlist is empty. Browse instruments:"),
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    await _do_news_fetch(message, parts[1].upper())


@router.callback_query(F.data.startswith("news_sym:"))
async def cb_news_symbol(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    await callback.answer()
    await callback.message.answer(f"📰 Fetching news for <b>{symbol}</b>...", parse_mode="HTML")
    try:
        loop = asyncio.get_running_loop()
        news = await loop.run_in_executor(None, lambda: get_news(symbol))
        await callback.message.answer(format_news_message(symbol, news), parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        await callback.message.answer(f"❌ Could not fetch news: {e}", parse_mode="HTML")


async def _do_news_fetch(message: Message, symbol: str):
    await message.answer(f"📰 Fetching news for <b>{symbol}</b>...", parse_mode="HTML")
    try:
        loop = asyncio.get_running_loop()
        news = await loop.run_in_executor(None, lambda: get_news(symbol))
        await message.answer(format_news_message(symbol, news), parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        await message.answer(f"❌ Could not fetch news: {e}", parse_mode="HTML")


# ── /history ──────────────────────────────────────────────────────────────────

@router.message(Command("history"))
async def cmd_history(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    async with AsyncSessionLocal() as session:
        signals = await get_recent_signals(session, message.from_user.id, limit=10)

    builder = InlineKeyboardBuilder()
    builder.button(text="🏆 Performance", callback_data="home:performance")
    await message.answer(format_history_message(signals), parse_mode="HTML", reply_markup=builder.as_markup())


# ── /performance ──────────────────────────────────────────────────────────────

@router.message(Command("performance"))
async def cmd_performance(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    async with AsyncSessionLocal() as session:
        stats = await get_performance_stats(session, message.from_user.id)

    builder = InlineKeyboardBuilder()
    builder.button(text="📜 Signal History", callback_data="home:history")
    await message.answer(format_performance_message(stats), parse_mode="HTML", reply_markup=builder.as_markup())


# ── /symbols ──────────────────────────────────────────────────────────────────

@router.message(Command("symbols"))
async def cmd_symbols(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(text=category, callback_data=f"cat:{category}")
    builder.adjust(2)

    await message.answer(
        "🔎 <b>Instruments</b>\n\nSelect a category:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(callback: CallbackQuery):
    category = callback.data[4:]
    symbols = CATEGORIES.get(category, [])

    async with AsyncSessionLocal() as session:
        watchlist = await get_watchlist(session, callback.from_user.id)

    builder = InlineKeyboardBuilder()
    for s in symbols:
        display = get_display_name(s)
        if s in watchlist:
            builder.button(text=f"✅ {s}", callback_data=f"noop:{s}")
        else:
            builder.button(text=f"+ {s}  {display}", callback_data=f"quickadd:{s}")
    builder.button(text="← Back", callback_data="symbols_back")
    builder.adjust(1)

    await callback.message.edit_text(
        f"<b>{category}</b>\n\nTap to add   ✅ = already in watchlist",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "symbols_back")
async def cb_symbols_back(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(text=category, callback_data=f"cat:{category}")
    builder.adjust(2)
    await callback.message.edit_text(
        "🔎 <b>Instruments</b>\n\nSelect a category:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("noop:"))
async def cb_noop(callback: CallbackQuery):
    await callback.answer("Already in your watchlist.", show_alert=False)


@router.callback_query(F.data.startswith("quickadd:"))
async def cb_quickadd(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        added = await add_symbol(session, callback.from_user.id, symbol)
    if added:
        await callback.answer(f"✅ {symbol} added.", show_alert=False)
        await callback.message.answer(
            f"✅ <b>{get_display_name(symbol)}</b> added to your watchlist.",
            parse_mode="HTML",
        )
    else:
        await callback.answer("Already in your watchlist.", show_alert=False)


# ── /alerts ───────────────────────────────────────────────────────────────────

@router.message(Command("alerts"))
async def cmd_alerts(message: Message):
    await _ensure_user(message)
    parts = message.text.split()

    async with AsyncSessionLocal() as session:
        if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
            settings = await get_settings(session, message.from_user.id)
            icon  = "🔔" if settings.alerts_enabled else "🔕"
            state = "ON" if settings.alerts_enabled else "OFF"
            builder = InlineKeyboardBuilder()
            builder.button(text="🔔 Turn On",  callback_data="set_alerts:on")
            builder.button(text="🔕 Turn Off", callback_data="set_alerts:off")
            builder.adjust(2)
            await message.answer(
                f"{icon} <b>Alerts are {state}</b>\n\nAuto-push signals when they fire.",
                parse_mode="HTML",
                reply_markup=builder.as_markup(),
            )
            return
        enabled = parts[1].lower() == "on"
        await update_settings(session, message.from_user.id, alerts_enabled=enabled)

    icon = "🔔" if enabled else "🔕"
    await message.answer(f"{icon} Alerts <b>{'enabled' if enabled else 'disabled'}</b>.", parse_mode="HTML")


@router.callback_query(F.data.startswith("set_alerts:"))
async def cb_set_alerts(callback: CallbackQuery):
    enabled = callback.data.split(":")[1] == "on"
    async with AsyncSessionLocal() as session:
        await update_settings(session, callback.from_user.id, alerts_enabled=enabled)
    icon = "🔔" if enabled else "🔕"
    await callback.message.edit_text(
        f"{icon} Alerts <b>{'enabled' if enabled else 'disabled'}</b>.",
        parse_mode="HTML",
    )
    await callback.answer()


# ── /settings ─────────────────────────────────────────────────────────────────

@router.message(Command("settings"))
async def cmd_settings(message: Message):
    await _ensure_user(message)
    async with AsyncSessionLocal() as session:
        s = await get_settings(session, message.from_user.id)
        watchlist = await get_watchlist(session, message.from_user.id)

    builder = InlineKeyboardBuilder()
    builder.button(text="🔔 Alerts",      callback_data="open_setting:alerts")
    builder.button(text="⏱ Timeframe",   callback_data="open_setting:timeframe")
    builder.button(text="🎯 Confluence",  callback_data="open_setting:confluence")
    builder.button(text="💰 Balance",     callback_data="open_setting:balance")
    builder.button(text="⚡ Risk",        callback_data="open_setting:risk")
    builder.adjust(2)

    await message.answer(
        format_settings_message(s, len(watchlist)),
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("open_setting:"))
async def cb_open_setting(callback: CallbackQuery):
    setting = callback.data.split(":")[1]
    await callback.answer()

    if setting == "alerts":
        async with AsyncSessionLocal() as session:
            s = await get_settings(session, callback.from_user.id)
        icon  = "🔔" if s.alerts_enabled else "🔕"
        state = "ON" if s.alerts_enabled else "OFF"
        builder = InlineKeyboardBuilder()
        builder.button(text="🔔 Turn On",  callback_data="set_alerts:on")
        builder.button(text="🔕 Turn Off", callback_data="set_alerts:off")
        builder.adjust(2)
        await callback.message.answer(
            f"{icon} <b>Alerts are {state}</b>\n\nAuto-push signals when they fire.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

    elif setting == "timeframe":
        valid = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
        builder = InlineKeyboardBuilder()
        for tf in valid:
            builder.button(text=tf, callback_data=f"set_tf:{tf}")
        builder.adjust(4)
        await callback.message.answer(
            "⏱ <b>Change Timeframe</b>\n\nRecommended: <b>1h</b> or <b>4h</b>",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

    elif setting == "confluence":
        labels = {1: "1  Very sensitive", 2: "2  Loose", 3: "3  Recommended", 4: "4  Strict"}
        builder = InlineKeyboardBuilder()
        for n, label in labels.items():
            builder.button(text=label, callback_data=f"set_conf:{n}")
        builder.adjust(1)
        await callback.message.answer(
            "🎯 <b>Signal Sensitivity</b>\n\nHow many indicators must agree before a signal fires:",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

    elif setting == "balance":
        builder = InlineKeyboardBuilder()
        for amt in [1000, 2000, 5000, 10000, 25000, 50000]:
            builder.button(text=f"${amt:,}", callback_data=f"set_bal:{amt}")
        builder.adjust(3)
        await callback.message.answer(
            "💰 <b>Account Balance</b>\n\nChoose a preset or type: <code>/balance 10000</code>",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

    elif setting == "risk":
        builder = InlineKeyboardBuilder()
        for pct in ["0.5", "1", "1.5", "2", "3", "5"]:
            builder.button(text=f"{pct}%", callback_data=f"set_risk:{pct}")
        builder.adjust(3)
        await callback.message.answer(
            "⚡ <b>Risk Per Trade</b>\n\nRecommended: <b>1% - 2%</b>\n\nOr type: <code>/risk 1.5</code>",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )


# ── /timeframe ────────────────────────────────────────────────────────────────

@router.message(Command("timeframe"))
async def cmd_timeframe(message: Message):
    await _ensure_user(message)
    valid = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    parts = message.text.split()
    if len(parts) < 2 or parts[1].lower() not in valid:
        builder = InlineKeyboardBuilder()
        for tf in valid:
            builder.button(text=tf, callback_data=f"set_tf:{tf}")
        builder.adjust(4)
        await message.answer(
            "⏱ <b>Change Timeframe</b>\n\nRecommended: <b>1h</b> or <b>4h</b>",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return
    async with AsyncSessionLocal() as session:
        await update_settings(session, message.from_user.id, timeframe=parts[1].lower())
    await message.answer(f"✅ Timeframe set to <b>{parts[1].lower()}</b>.", parse_mode="HTML")


@router.callback_query(F.data.startswith("set_tf:"))
async def cb_set_timeframe(callback: CallbackQuery):
    tf = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        await update_settings(session, callback.from_user.id, timeframe=tf)
    await callback.message.edit_text(f"✅ Timeframe set to <b>{tf}</b>.", parse_mode="HTML")
    await callback.answer()


# ── /confluence ───────────────────────────────────────────────────────────────

@router.message(Command("confluence"))
async def cmd_confluence(message: Message):
    await _ensure_user(message)
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit() or not 1 <= int(parts[1]) <= 4:
        labels = {1: "1  Very sensitive", 2: "2  Loose", 3: "3  Recommended", 4: "4  Strict"}
        builder = InlineKeyboardBuilder()
        for n, label in labels.items():
            builder.button(text=label, callback_data=f"set_conf:{n}")
        builder.adjust(1)
        await message.answer(
            "🎯 <b>Signal Sensitivity</b>\n\nHow many indicators must agree before a signal fires:",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return
    async with AsyncSessionLocal() as session:
        await update_settings(session, message.from_user.id, min_confluence=int(parts[1]))
    await message.answer(f"✅ Confluence set to <b>{parts[1]} of 4</b>.", parse_mode="HTML")


@router.callback_query(F.data.startswith("set_conf:"))
async def cb_set_confluence(callback: CallbackQuery):
    n = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        await update_settings(session, callback.from_user.id, min_confluence=n)
    await callback.message.edit_text(f"✅ Confluence set to <b>{n} of 4</b>.", parse_mode="HTML")
    await callback.answer()


# ── /balance ──────────────────────────────────────────────────────────────────

@router.message(Command("balance"))
async def cmd_balance(message: Message):
    await _ensure_user(message)
    parts = message.text.split()
    if len(parts) < 2:
        builder = InlineKeyboardBuilder()
        for amt in [1000, 2000, 5000, 10000, 25000, 50000]:
            builder.button(text=f"${amt:,}", callback_data=f"set_bal:{amt}")
        builder.adjust(3)
        await message.answer(
            "💰 <b>Account Balance</b>\n\nChoose a preset or type the amount:\n<code>/balance 10000</code>",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return
    try:
        bal = float(parts[1])
    except ValueError:
        await message.answer("❌ Enter a valid number. Example: <code>/balance 10000</code>", parse_mode="HTML")
        return
    async with AsyncSessionLocal() as session:
        await update_settings(session, message.from_user.id, account_balance=bal)
    await message.answer(f"✅ Balance set to <b>${bal:,.0f}</b>.", parse_mode="HTML")


@router.callback_query(F.data.startswith("set_bal:"))
async def cb_set_balance(callback: CallbackQuery):
    bal = float(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        await update_settings(session, callback.from_user.id, account_balance=bal)
    await callback.message.edit_text(f"✅ Balance set to <b>${bal:,.0f}</b>.", parse_mode="HTML")
    await callback.answer()


# ── /risk ─────────────────────────────────────────────────────────────────────

@router.message(Command("risk"))
async def cmd_risk(message: Message):
    await _ensure_user(message)
    parts = message.text.split()
    if len(parts) < 2:
        builder = InlineKeyboardBuilder()
        for pct in ["0.5", "1", "1.5", "2", "3", "5"]:
            builder.button(text=f"{pct}%", callback_data=f"set_risk:{pct}")
        builder.adjust(3)
        await message.answer(
            "⚡ <b>Risk Per Trade</b>\n\nRecommended: <b>1% - 2%</b>\n\nOr type: <code>/risk 1.5</code>",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return
    try:
        risk = float(parts[1])
        if not 0.1 <= risk <= 10:
            raise ValueError
    except ValueError:
        await message.answer("❌ Enter a number between 0.1 and 10.", parse_mode="HTML")
        return
    async with AsyncSessionLocal() as session:
        await update_settings(session, message.from_user.id, risk_percent=risk)
    await message.answer(f"✅ Risk set to <b>{risk}%</b> per trade.", parse_mode="HTML")


@router.callback_query(F.data.startswith("set_risk:"))
async def cb_set_risk(callback: CallbackQuery):
    risk = float(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        await update_settings(session, callback.from_user.id, risk_percent=risk)
    await callback.message.edit_text(f"✅ Risk set to <b>{risk}%</b> per trade.", parse_mode="HTML")
    await callback.answer()


# ── Access request callback ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("request_access:"))
async def cb_request_access(callback: CallbackQuery):
    user_id  = callback.from_user.id
    name     = callback.from_user.first_name or callback.from_user.username or str(user_id)
    username = f"@{callback.from_user.username}" if callback.from_user.username else "no username"

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Approve", callback_data=f"approve_user:{user_id}")
    builder.button(text="❌ Reject",  callback_data=f"reject_user:{user_id}")
    builder.adjust(2)

    try:
        await callback.bot.send_message(
            cfg.OWNER_CHAT_ID,
            f"🔔 <b>Access Request</b>\n\n"
            f"👤 <b>{name}</b>  {username}\n"
            f"🆔 <code>{user_id}</code>",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        await callback.answer("Request sent. You'll be notified once approved.", show_alert=True)
    except Exception as e:
        logger.error(f"Failed to notify owner: {e}")
        await callback.answer("Error sending request. Try again later.", show_alert=True)
