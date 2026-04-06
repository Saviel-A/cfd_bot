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


async def _check_access(message: Message) -> bool:
    async with AsyncSessionLocal() as session:
        allowed = await is_premium_or_owner(session, message.from_user.id, cfg.OWNER_CHAT_ID)
    if not allowed:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔑 Request Access", callback_data=f"request_access:{message.from_user.id}")
        await message.answer(
            "🔒 <b>Access Required</b>\n\n"
            "This is a private signal bot.\n"
            "Tap below to send an access request.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
    return allowed


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


# ── /start & /help ────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    await _ensure_user(message)
    is_owner = message.from_user.id == cfg.OWNER_CHAT_ID

    async with AsyncSessionLocal() as session:
        allowed = await is_premium_or_owner(session, message.from_user.id, cfg.OWNER_CHAT_ID)

    if not allowed:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔑 Request Access", callback_data=f"request_access:{message.from_user.id}")
        await message.answer(
            "📡 <b>CFD Smart Signal Bot</b>\n\n"
            "Multi-timeframe CFD signals with automatic SL and TP levels.\n\n"
            "🔒 This is a private bot. Tap below to request access.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    admin_section = (
        "\n\n🔧 <b>Admin</b>\n"
        "/users\n"
        "/approve 123456\n"
        "/revoke 123456\n"
        "/broadcast message"
    ) if is_owner else ""

    await message.answer(
        "📡 <b>CFD Smart Signal Bot</b>\n\n"
        "📈 <b>Signals</b>\n"
        "/signal XAUUSD\n"
        "/watchlist\n"
        "/symbols\n"
        "/add XAUUSD\n"
        "/remove XAUUSD\n\n"
        "📰 <b>Analysis</b>\n"
        "/news XAUUSD\n"
        "/history\n"
        "/performance\n\n"
        "⚙️ <b>Settings</b>\n"
        "/alerts on  |  /alerts off\n"
        "/timeframe 1h\n"
        "/confluence 3\n"
        "/settings"
        + admin_section,
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await cmd_start(message)


# ── /watchlist ────────────────────────────────────────────────────────────────

@router.message(Command("watchlist"))
async def cmd_watchlist(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    async with AsyncSessionLocal() as session:
        watchlist = await get_watchlist(session, message.from_user.id)

    if not watchlist:
        await message.answer("Your watchlist is empty.\n\nUse /add XAUUSD to add a symbol.", parse_mode="HTML")
        return

    await message.answer("Scanning...", parse_mode="HTML")
    results = []
    for symbol in watchlist:
        try:
            r = await _scan_symbol_for_user(symbol, message.from_user.id)
            results.append(r)
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e), "display_name": symbol, "signal": None})

    await message.answer(format_watchlist_message(results), parse_mode="HTML")


# ── /add ──────────────────────────────────────────────────────────────────────

@router.message(Command("add"))
async def cmd_add(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "<b>Add a Symbol</b>\n\n"
            "Usage: /add SYMBOL\n\n"
            "/add XAUUSD\n"
            "/add US30\n"
            "/add EURUSD\n"
            "/add BTC\n\n"
            "Browse all instruments: /symbols",
            parse_mode="HTML",
        )
        return

    symbol = parts[1].upper()
    ticker = get_ticker_for_symbol(symbol)
    await message.answer("Checking symbol...", parse_mode="HTML")

    try:
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, lambda: fetch_ohlcv(ticker, timeframe="1h", lookback=10))
        if df.empty:
            raise ValueError("No data")
    except Exception:
        await message.answer(
            f"Could not find data for <b>{symbol}</b>. Check the symbol and try again.",
            parse_mode="HTML",
        )
        return

    async with AsyncSessionLocal() as session:
        added = await add_symbol(session, message.from_user.id, symbol)

    if added:
        await message.answer(f"{get_display_name(symbol)} added to your watchlist.", parse_mode="HTML")
    else:
        await message.answer(f"{symbol} is already in your watchlist.", parse_mode="HTML")


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
            await message.answer("Your watchlist is empty.", parse_mode="HTML")
            return
        builder = InlineKeyboardBuilder()
        for s in watchlist:
            builder.button(text=f"✕ {s}", callback_data=f"remove:{s}")
        builder.adjust(2)
        await message.answer("Which symbol would you like to remove?", reply_markup=builder.as_markup())
        return

    symbol = parts[1].upper()
    async with AsyncSessionLocal() as session:
        removed = await remove_symbol(session, message.from_user.id, symbol)
    if removed:
        await message.answer(f"<b>{symbol}</b> removed.", parse_mode="HTML")
    else:
        await message.answer(f"<b>{symbol}</b> is not in your watchlist.", parse_mode="HTML")


@router.callback_query(F.data.startswith("remove:"))
async def cb_remove(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        await remove_symbol(session, callback.from_user.id, symbol)
    await callback.message.edit_text(f"<b>{symbol}</b> removed.", parse_mode="HTML")
    await callback.answer()


# ── /signal ───────────────────────────────────────────────────────────────────

@router.message(Command("signal"))
async def cmd_signal(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Send /signal followed by a symbol. Example: /signal XAUUSD", parse_mode="HTML")
        return

    symbol = parts[1].upper()
    await message.answer(f"Scanning <b>{symbol}</b>...", parse_mode="HTML")
    try:
        r = await _scan_symbol_for_user(symbol, message.from_user.id)
        if r["signal"].direction in ("BUY", "SELL"):
            text = format_signal_message(r["display_name"], r["signal"], r["trade"])
        else:
            text = format_hold_message(r["display_name"], r["signal"])
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"Could not scan <b>{symbol}</b>: {e}", parse_mode="HTML")


# ── /news ─────────────────────────────────────────────────────────────────────

@router.message(Command("news"))
async def cmd_news(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Send /news followed by a symbol. Example: /news XAUUSD", parse_mode="HTML")
        return

    symbol = parts[1].upper()
    await message.answer(f"Fetching news for <b>{symbol}</b>...", parse_mode="HTML")
    try:
        loop = asyncio.get_running_loop()
        news = await loop.run_in_executor(None, lambda: get_news(symbol))
        await message.answer(format_news_message(symbol, news), parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        await message.answer(f"Could not fetch news: {e}", parse_mode="HTML")


# ── /history ──────────────────────────────────────────────────────────────────

@router.message(Command("history"))
async def cmd_history(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    async with AsyncSessionLocal() as session:
        signals = await get_recent_signals(session, message.from_user.id, limit=10)

    await message.answer(format_history_message(signals), parse_mode="HTML")


# ── /performance ──────────────────────────────────────────────────────────────

@router.message(Command("performance"))
async def cmd_performance(message: Message):
    await _ensure_user(message)
    if not await _check_access(message): return

    async with AsyncSessionLocal() as session:
        stats = await get_performance_stats(session, message.from_user.id)

    await message.answer(format_performance_message(stats), parse_mode="HTML")


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
        "<b>Instruments</b>\n\n"
        "Select a category to browse symbols.\n"
        "Tap any symbol to add it to your watchlist.\n\n"
        "Or add directly:  <code>/add XAUUSD</code>  <code>/add BTC</code>",
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
    builder.button(text="←", callback_data="symbols_back")
    builder.adjust(1)

    await callback.message.edit_text(
        f"<b>{category}</b>\n\nTap + to add   ✅ already in watchlist",
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
        "<b>Instruments</b>\n\nSelect a category to browse symbols.",
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
        await callback.answer(f"{symbol} added.", show_alert=False)
        await callback.message.answer(f"{get_display_name(symbol)} added to your watchlist.", parse_mode="HTML")
    else:
        await callback.answer("Already in watchlist.", show_alert=False)


# ── /alerts ───────────────────────────────────────────────────────────────────

@router.message(Command("alerts"))
async def cmd_alerts(message: Message):
    await _ensure_user(message)
    parts = message.text.split()

    async with AsyncSessionLocal() as session:
        if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
            settings = await get_settings(session, message.from_user.id)
            state = "ON" if settings.alerts_enabled else "OFF"
            await message.answer(
                f"Alerts are currently <b>{state}</b>.\n"
                "Use /alerts on or /alerts off.",
                parse_mode="HTML",
            )
            return
        enabled = parts[1].lower() == "on"
        await update_settings(session, message.from_user.id, alerts_enabled=enabled)

    await message.answer(f"Alerts {'enabled' if enabled else 'disabled'}.", parse_mode="HTML")


# ── /settings ─────────────────────────────────────────────────────────────────

@router.message(Command("settings"))
async def cmd_settings(message: Message):
    await _ensure_user(message)
    async with AsyncSessionLocal() as session:
        s = await get_settings(session, message.from_user.id)
        watchlist = await get_watchlist(session, message.from_user.id)

    await message.answer(format_settings_message(s, len(watchlist)), parse_mode="HTML")


# ── /timeframe ────────────────────────────────────────────────────────────────

@router.message(Command("timeframe"))
async def cmd_timeframe(message: Message):
    await _ensure_user(message)
    valid = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    parts = message.text.split()
    if len(parts) < 2 or parts[1].lower() not in valid:
        await message.answer("Choose a timeframe: 1m  5m  15m  30m  1h  4h  1d", parse_mode="HTML")
        return
    async with AsyncSessionLocal() as session:
        await update_settings(session, message.from_user.id, timeframe=parts[1].lower())
    await message.answer(f"Timeframe updated to {parts[1].lower()}.", parse_mode="HTML")


# ── /confluence ───────────────────────────────────────────────────────────────

@router.message(Command("confluence"))
async def cmd_confluence(message: Message):
    await _ensure_user(message)
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit() or not 1 <= int(parts[1]) <= 4:
        await message.answer("Send a number from 1 to 4. Recommended: 3", parse_mode="HTML")
        return
    async with AsyncSessionLocal() as session:
        await update_settings(session, message.from_user.id, min_confluence=int(parts[1]))
    await message.answer(f"Confluence updated to {parts[1]}.", parse_mode="HTML")


# ── /balance ──────────────────────────────────────────────────────────────────

@router.message(Command("balance"))
async def cmd_balance(message: Message):
    await _ensure_user(message)
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /balance 10000", parse_mode="HTML")
        return
    try:
        bal = float(parts[1])
    except ValueError:
        await message.answer("Enter a valid number.", parse_mode="HTML")
        return
    async with AsyncSessionLocal() as session:
        await update_settings(session, message.from_user.id, account_balance=bal)
    await message.answer(f"Balance updated to ${bal:,.0f}.", parse_mode="HTML")


# ── /risk ─────────────────────────────────────────────────────────────────────

@router.message(Command("risk"))
async def cmd_risk(message: Message):
    await _ensure_user(message)
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /risk 1.5  (percentage per trade)", parse_mode="HTML")
        return
    try:
        risk = float(parts[1])
        if not 0.1 <= risk <= 10:
            raise ValueError
    except ValueError:
        await message.answer("Enter a number between 0.1 and 10.", parse_mode="HTML")
        return
    async with AsyncSessionLocal() as session:
        await update_settings(session, message.from_user.id, risk_percent=risk)
    await message.answer(f"Risk updated to {risk}% per trade.", parse_mode="HTML")


# ── Access request callback ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("request_access:"))
async def cb_request_access(callback: CallbackQuery):
    user_id = callback.from_user.id
    name = callback.from_user.first_name or callback.from_user.username or str(user_id)

    builder = InlineKeyboardBuilder()
    builder.button(text="Approve", callback_data=f"approve_user:{user_id}")
    builder.button(text="Reject",  callback_data=f"reject_user:{user_id}")

    username = f"@{callback.from_user.username}" if callback.from_user.username else "no username"
    try:
        await callback.bot.send_message(
            cfg.OWNER_CHAT_ID,
            f"🔔 <b>Access Request</b>\n\n"
            f"Name    <b>{name}</b>\n"
            f"ID      <code>{user_id}</code>\n"
            f"User    {username}",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        await callback.answer("Request submitted. You will be notified once approved.", show_alert=True)
    except Exception as e:
        logger.error(f"Failed to notify owner: {e}")
        await callback.answer("Error sending request. Try again later.", show_alert=True)
