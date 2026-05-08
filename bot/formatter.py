"""Telegram message formatter."""

from src.signal_engine import Signal
from src.risk_manager import TradeParams
from src.trading_hours import symbol_market_status
from src.instruments import get_symbol_label
from typing import Optional


def _fmt_alert(price: float) -> str:
    """Format signal levels with enough precision for execution."""
    if price < 10:
        return f"{price:.5f}".rstrip("0").rstrip(".")
    return f"{price:,.2f}"


def _fmt_price(price: float) -> str:
    """For live price display: always show 2 decimal places."""
    if price < 10:
        return f"{price:.5f}".rstrip("0").rstrip(".")
    return f"{price:,.2f}"


def _fmt_distance(value: float) -> str:
    if value < 1:
        return f"{value:.5f}".rstrip("0").rstrip(".")
    return f"{value:,.2f}"


def _vote_label(value: int) -> str:
    if value == 1:
        return "Bullish"
    if value == -1:
        return "Bearish"
    return "Neutral"


def _news_line(news_risk: str, news_events: Optional[list]) -> str:
    if news_risk == "WARN" and news_events:
        titles = ", ".join(e["title"] for e in news_events[:2])
        return f"News: <b>Watch</b> ({titles})"
    return "News: <b>Clear</b>"


def _pressure_line(market_pressure: Optional[dict]) -> str:
    if not market_pressure:
        return "Market pressure: <b>Not checked</b>"
    direction = market_pressure.get("direction", "MIXED").title()
    buy_pct = market_pressure.get("buy_pct", 50)
    sell_pct = market_pressure.get("sell_pct", 50)
    if direction == "Buyers":
        return f"Market pressure: <b>Buyers {buy_pct:.0f}%</b>"
    if direction == "Sellers":
        return f"Market pressure: <b>Sellers {sell_pct:.0f}%</b>"
    return f"Market pressure: <b>Mixed</b> ({buy_pct:.0f}% buy / {sell_pct:.0f}% sell)"


# Signal card
def format_signal_message(
    display_name: str,
    signal: Signal,
    trade: Optional[TradeParams],
    symbol: str = "",
    live_price: Optional[float] = None,
    news_risk: str = "CLEAR",
    news_events: Optional[list] = None,
    market_pressure: Optional[dict] = None,
) -> str:
    name   = get_symbol_label(symbol) if symbol else display_name
    is_buy = signal.direction == "BUY"
    arrow  = "📈" if is_buy else "📉"
    label  = "BUY" if is_buy else "SELL"

    entry_price = live_price if live_price else signal.current_price
    entry = f"<code>{_fmt_alert(entry_price)}</code>"
    sl    = f"<code>{_fmt_alert(trade.stop_loss)}</code>" if trade else "N/A"
    tp1   = f"<code>{_fmt_alert(trade.tp1)}</code>"       if trade else "N/A"
    tp2   = f"<code>{_fmt_alert(trade.tp2)}</code>"       if trade else "N/A"
    tp3   = f"<code>{_fmt_alert(trade.tp3)}</code>"       if trade else "N/A"
    risk  = f"<code>{_fmt_distance(trade.sl_distance)}</code>" if trade else "N/A"

    votes = signal.details or {}
    ema = _vote_label(votes.get("EMA trend", 0))
    macd = _vote_label(votes.get("MACD", 0))
    rsi = _vote_label(votes.get("RSI", 0))
    news = _news_line(news_risk, news_events)
    pressure = _pressure_line(market_pressure)
    risk_note = "Risk distance: distance from entry to Stop Loss"

    return (
        f"{arrow} <b>{name} {label}</b>\n"
        f"\n"
        f"<b>New Signal</b>\n"
        f"Timeframe: <b>1H entry, 4H trend</b>\n"
        f"Status: <b>Active</b>\n"
        f"{news}\n"
        f"{pressure}\n"
        f"\n"
        f"<b>Trade Plan</b>\n"
        f"Entry: {entry}\n"
        f"Stop Loss: {sl}\n"
        f"TP1: {tp1}\n"
        f"TP2: {tp2}\n"
        f"TP3: {tp3}\n"
        f"\n"
        f"<b>Setup</b>\n"
        f"4H trend: <b>{signal.htf_bias.title()}</b>\n"
        f"EMA: {ema}  MACD: {macd}  RSI: {rsi}\n"
        f"\n"
        f"{risk_note}: {risk}\n"
        f"<i>Manage risk. Not financial advice.</i>"
    )


# Market closed card
def format_market_closed_message(display_name: str, price: Optional[float], symbol: str = "") -> str:
    name      = get_symbol_label(symbol) if symbol else display_name
    price_str = f"<b>{_fmt_alert(price)}</b>" if price else "N/A"
    is_open, status = symbol_market_status(symbol or display_name)
    icon = "🟢" if is_open else "🔴"
    return (
        f"{icon} <b>{name}</b>\n"
        f"\n"
        f"{status}\n"
        f"Last price: {price_str}"
    )


# Hold card
def format_hold_message(display_name: str, signal: Signal, symbol: str = "") -> str:
    is_open, mkt = symbol_market_status(symbol or display_name)
    reason = signal.reason or "No clean setup"
    name = get_symbol_label(symbol) if symbol else display_name
    return (
        f"⚪ <b>{name}</b>\n"
        f"{mkt}\n"
        f"No signal at this time.\n"
        f"Reason: {reason}"
    )


# Watchlist
def format_watchlist_message(results: list) -> str:
    if not results:
        return "📋 <b>Watchlist</b>\n\nNo symbols yet. Use /add to start tracking."

    lines = ["📋 <b>Watchlist</b>", ""]
    for r in results:
        symbol  = r.get("symbol", "")
        name    = get_symbol_label(symbol) if symbol else r["display_name"]
        if r.get("error"):
            lines.append(f"⚠️ <b>{name}</b>  unavailable")
            continue
        signal  = r["signal"]
        price   = f"<code>{_fmt_price(signal.current_price)}</code>" if signal.current_price else "N/A"
        is_open, _ = symbol_market_status(symbol)
        dot = "🟢" if is_open else "🔴"

        if signal.direction == "BUY":
            badge = "📈 BUY"
        elif signal.direction == "SELL":
            badge = "📉 SELL"
        else:
            badge = "No Signal"

        lines.append(f"{dot} <b>{name}</b>  {price}  {badge}")

    return "\n".join(lines)


def format_stats_message(stats: dict) -> str:
    if not stats["total"]:
        return "📊 <b>Stats</b>\n\nNo signals yet. Run the bot, collect outcomes, then use /stats."

    closed = stats["closed"]
    win_rate = (stats["wins"] / closed * 100) if closed else 0

    lines = [
        f"📊 <b>Signal Stats</b>  last {stats['total']} signals",
        "",
        f"Signals: <b>{stats['total']}</b>",
        f"Open: <b>{stats['open']}</b>",
        f"Closed: <b>{closed}</b>",
        f"Wins: <b>{stats['wins']}</b>  Losses: <b>{stats['losses']}</b>  Expired: <b>{stats['expired']}</b>",
        f"Win rate: <b>{win_rate:.1f}%</b>",
        "",
        f"Targets: TP1 {stats['tp1']}, TP2 {stats['tp2']}, TP3 {stats['tp3']}",
    ]

    if stats["best_symbols"]:
        best = ", ".join(f"{s['symbol']} ({s['wins']}/{s['total']})" for s in stats["best_symbols"])
        lines.extend(["", f"Best: <b>{best}</b>"])

    if stats["worst_symbols"]:
        worst = ", ".join(f"{s['symbol']} ({s['losses']}L/{s['total']})" for s in stats["worst_symbols"] if s["losses"])
        if worst:
            lines.append(f"Worst: <b>{worst}</b>")

    return "\n".join(lines)


# Signal history
def format_history_message(signals: list, limit: int = 20) -> str:
    if not signals:
        return "📋 <b>Signal History</b>\n\nNo signals fired yet."

    outcome_label = {
        "OPEN":    "⏳ Open",
        "TP1":     "🎯 TP1",
        "TP2":     "🎯 TP2",
        "TP3":     "🎯 TP3",
        "SL":      "❌ SL",
        "EXPIRED": "⏸ Expired",
    }

    lines = [f"📋 <b>Signal History</b>  ({len(signals)})", ""]
    for s in signals:
        dot     = "🟢" if s.direction == "BUY" else "🔴"
        outcome = outcome_label.get(s.outcome, s.outcome)
        try:
            from datetime import datetime, timezone
            from zoneinfo import ZoneInfo
            dt = s.fired_at
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            date = dt.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%d %B  %H:%M")
        except Exception:
            date = str(s.fired_at)[:16]
        lines.append(
            f"{dot} <b>{s.symbol}</b>  {outcome}\n"
            f"    <code>{_fmt_alert(float(s.entry_price))}</code>  {date}"
        )

    return "\n".join(lines)
