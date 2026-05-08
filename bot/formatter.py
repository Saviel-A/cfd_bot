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


def _format_reason(reason: str) -> str:
    if not reason:
        return "No clean setup"
    clean = reason.strip()
    replacements = {
        "BUY blocked: counter-trend to 4H bearish bias": "Buy blocked: 4H trend is bearish",
        "SELL blocked: counter-trend to 4H bullish bias": "Sell blocked: 4H trend is bullish",
        "BUY blocked: RSI is overbought": "Buy blocked: RSI is overbought",
        "SELL blocked: RSI is oversold": "Sell blocked: RSI is oversold",
        "4H bullish trend + 1H bullish confirmation": "4H bullish + 1H confirms",
        "4H bearish trend + 1H bearish confirmation": "4H bearish + 1H confirms",
        "Not enough aligned confirmation": "Not enough confirmation",
        "4H trend is neutral": "4H trend is neutral",
    }
    return replacements.get(clean, clean[:1].upper() + clean[1:])


def _news_line(news_risk: str, news_events: Optional[list]) -> str:
    if news_risk == "WARN" and news_events:
        return "News watch"
    return "News clear"


def _pressure_line(market_pressure: Optional[dict]) -> str:
    if not market_pressure:
        return "Pressure unknown"
    direction = market_pressure.get("direction", "MIXED").title()
    buy_pct = market_pressure.get("buy_pct", 50)
    sell_pct = market_pressure.get("sell_pct", 50)
    if direction == "Buyers":
        return f"Buyers {buy_pct:.0f}%"
    if direction == "Sellers":
        return f"Sellers {sell_pct:.0f}%"
    return f"Mixed pressure"


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

    news = _news_line(news_risk, news_events)
    pressure = _pressure_line(market_pressure)
    reason = _format_reason(signal.reason)

    return (
        f"{arrow} <b>{name} {label}</b>\n\n"
        f"Entry: {entry}\n"
        f"SL: {sl}  (risk {risk})\n"
        f"TP: {tp1} / {tp2} / {tp3}\n\n"
        f"Why: {reason}\n"
        f"Check: {news} | {pressure}\n\n"
        f"<i>If SL hits, exit. Not financial advice.</i>"
    )


# Market closed card
def format_market_closed_message(display_name: str, price: Optional[float], symbol: str = "") -> str:
    name      = get_symbol_label(symbol) if symbol else display_name
    price_str = f"<b>{_fmt_alert(price)}</b>" if price else "N/A"
    is_open, status = symbol_market_status(symbol or display_name)
    icon = "🟢" if is_open else "🔴"
    return (
        f"{icon} <b>{name} Market closed</b>\n\n"
        f"{status}\n"
        f"Price: {price_str}\n"
        "No alert will be sent."
    )


# Hold card
def format_hold_message(display_name: str, signal: Signal, symbol: str = "") -> str:
    is_open, mkt = symbol_market_status(symbol or display_name)
    reason = signal.reason or "No clean setup"
    name = get_symbol_label(symbol) if symbol else display_name
    return (
        f"⚪ <b>{name} No signal</b>\n\n"
        f"Reason: {_format_reason(reason)}\n"
        f"{mkt}"
    )


# Watchlist
def format_watchlist_message(results: list) -> str:
    if not results:
        return "📋 <b>Watchlist</b>\n\nEmpty. Use /add XAUUSD."

    lines = ["📋 <b>Watchlist</b>"]
    for r in results:
        symbol  = r.get("symbol", "")
        name    = get_symbol_label(symbol) if symbol else r["display_name"]
        if r.get("error"):
            lines.append(f"⚠️ <b>{name}</b>: unavailable")
            continue
        signal  = r["signal"]
        price   = f"<code>{_fmt_price(signal.current_price)}</code>" if signal.current_price else "N/A"
        is_open, _ = symbol_market_status(symbol)
        dot = "🟢" if is_open else "🔴"

        if signal.direction == "BUY":
            badge = "📈 BUY"
            reason = "Aligned"
        elif signal.direction == "SELL":
            badge = "📉 SELL"
            reason = "Aligned"
        else:
            badge = "⚪ No signal"
            reason = _format_reason(signal.reason or "No clean setup")

        lines.append(
            f"\n{dot} <b>{name}</b>\n"
            f"{badge} | Price {price}\n"
            f"{reason}"
        )

    return "\n".join(lines)


def format_stats_message(stats: dict) -> str:
    if not stats["total"]:
        return "📊 <b>Stats</b>\n\nNo signals yet. Run the bot, collect outcomes, then use /stats."

    closed = stats["closed"]
    win_rate = (stats["wins"] / closed * 100) if closed else 0

    lines = [
        "📊 <b>Performance</b>",
        f"Signals: <b>{stats['total']}</b> | Open: <b>{stats['open']}</b>",
        f"Closed: <b>{closed}</b> | Win rate: <b>{win_rate:.1f}%</b>",
        f"Wins: <b>{stats['wins']}</b> | Losses: <b>{stats['losses']}</b> | Expired: <b>{stats['expired']}</b>",
        f"TP hits: <b>{stats['tp1']}</b> / <b>{stats['tp2']}</b> / <b>{stats['tp3']}</b>",
    ]

    if stats["best_symbols"]:
        lines.extend(["", "<b>Best</b>"])
        for s in stats["best_symbols"]:
            lines.append(
                    f"{s['symbol']}: {s['wins']} wins / {s['total']} closed"
            )

    if stats["worst_symbols"]:
        worst_symbols = [s for s in stats["worst_symbols"] if s["losses"]]
        if worst_symbols:
            lines.extend(["", "<b>Review</b>"])
            for s in worst_symbols:
                lines.append(
                    f"{s['symbol']}: {s['losses']} losses / {s['total']} closed"
                )

    return "\n".join(lines)


# Signal history
def format_history_message(signals: list, limit: int = 20) -> str:
    if not signals:
        return "📋 <b>Signal History</b>\n\nNo signals fired yet."

    outcome_label = {
        "OPEN":    "⏳ Still open",
        "TP1":     "🎯 TP1 reached",
        "TP2":     "🎯 TP2 reached",
        "TP3":     "🏆 TP3 reached",
        "SL":      "❌ Stop Loss hit",
        "EXPIRED": "⏸ Expired",
        "SUPERSEDED": "🔁 Replaced by newer signal",
    }

    lines = [f"📋 <b>Signal History</b>", f"Showing: <b>{len(signals)}</b>"]
    for s in signals:
        dot     = "📈" if s.direction == "BUY" else "📉"
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
            f"\n{dot} <b>{s.symbol} {s.direction}</b> | {outcome}\n"
            f"Entry { _fmt_alert(float(s.entry_price)) } | {date}"
        )

    return "\n".join(lines)


def format_scan_summary(
    checked: int,
    broadcasts: int,
    holds: list[str] | None = None,
    errors: list[str] | None = None,
) -> str:
    holds = holds or []
    errors = errors or []
    lines = [
        "📡 <b>Scan Done</b>",
        f"Checked: <b>{checked}</b> | Sent: <b>{broadcasts}</b>",
    ]

    if broadcasts:
        lines.append("Channel updated.")
    else:
        lines.extend(["", "<b>No alert sent</b>"])
        if holds:
            for item in holds[:8]:
                if ":" in item:
                    symbol, reason = item.split(":", 1)
                    lines.append(f"• {symbol.strip()}: {_format_reason(reason.strip())}")
                else:
                    lines.append(f"• {_format_reason(item)}")
        else:
            lines.append("No clean setup matched the strategy rules.")

    if errors:
        lines.extend(["", "<b>Errors</b>"])
        lines.extend(errors[:5])

    return "\n".join(lines)
