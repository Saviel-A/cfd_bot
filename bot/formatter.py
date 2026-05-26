"""Telegram message formatter."""

from src.signal_engine import Signal
from src.risk_manager import TradeParams
from src.trading_hours import symbol_market_status
from src.instruments import get_symbol_label
from typing import Optional


def _fmt_alert(price: float) -> str:
    if price < 10:
        return f"{price:.5f}".rstrip("0").rstrip(".")
    return f"{int(round(price)):,}"


def _fmt_price(price: float) -> str:
    if price < 10:
        return f"{price:.5f}".rstrip("0").rstrip(".")
    return f"{int(round(price)):,}"


def _fmt_distance(value: float) -> str:
    if value < 1:
        return f"{value:.5f}".rstrip("0").rstrip(".")
    return f"{int(round(value))}"


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
        "4H bullish + full 1H bearish override": "Strong reversal — all indicators bearish",
        "4H bearish + full 1H bullish override": "Strong reversal — all indicators bullish",
        "1H bearish + seller momentum": "1H bearish + seller momentum",
        "1H bullish + buyer momentum": "1H bullish + buyer momentum",
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

    news = _news_line(news_risk, news_events)
    pressure = _pressure_line(market_pressure)
    reason = _format_reason(signal.reason)

    return (
        f"{arrow} <b>{name} {label}</b>\n"
        f"Setup: {reason}\n\n"
        f"Entry: {entry}\n"
        f"SL: {sl}\n"
        f"TP1: {tp1}\n"
        f"TP2: {tp2}\n"
        f"TP3: {tp3}\n\n"
        f"{news} | {pressure}"
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
        return "📊 <b>Stats</b>\n\nNo signals yet. Run the bot, collect outcomes, then check back."

    closed = stats["closed"]
    win_rate = (stats["wins"] / closed * 100) if closed else 0
    tp_wins = stats["tp1"] + stats["tp2"] + stats["tp3"]

    lines = [
        "📊 <b>Stats</b>",
        f"Last <b>{stats['total']}</b> signals",
        f"Open: <b>{stats['open']}</b> | Closed: <b>{closed}</b>",
        f"Win rate: <b>{win_rate:.1f}%</b>",
        "",
        "<b>Outcomes</b>",
        f"✅ Wins: <b>{stats['wins']}</b>  |  🛑 SL: <b>{stats['losses']}</b>  |  ⏸ Expired: <b>{stats['expired']}</b>",
        f"🎯 TP1: <b>{stats['tp1']}</b>  |  🎯 TP2: <b>{stats['tp2']}</b>  |  🏆 TP3: <b>{stats['tp3']}</b>",
    ]

    if tp_wins:
        lines.append(f"Best outcome: <b>{_best_tp(stats)}</b>")

    if stats["best_symbols"]:
        lines.extend(["", "<b>By Symbol</b>"])
        for s in stats["best_symbols"]:
            lines.append(
                f"{s['symbol']}: {s['wins']}W {s['losses']}L | TP {s['tp1']}/{s['tp2']}/{s['tp3']}"
            )

    recent_closed = stats.get("recent_closed") or []
    if recent_closed:
        lines.extend(["", "<b>Recent Results</b>"])
        for s in recent_closed[:5]:
            lines.append(_format_result_line(s))

    return "\n".join(lines)


def _best_tp(stats: dict) -> str:
    outcomes = [("TP3", stats["tp3"]), ("TP2", stats["tp2"]), ("TP1", stats["tp1"])]
    best, count = max(outcomes, key=lambda item: item[1])
    return f"{best} ({count})"


def _format_result_line(signal) -> str:
    label = {
        "TP1": "✅ TP1",
        "TP2": "✅ TP2",
        "TP3": "🏆 TP3",
        "SL": "🛑 SL",
        "EXPIRED": "⏸ Expired",
        "SUPERSEDED": "🔁 Replaced",
        "OPEN": "⏳ Open",
    }.get(signal.outcome, signal.outcome)
    arrow = "📈" if signal.direction == "BUY" else "📉"
    return f"{arrow} {signal.symbol} {signal.direction}: <b>{label}</b>"


# Recent alerts
def format_history_message(signals: list, limit: int = 20) -> str:
    if not signals:
        return "📋 <b>Recent Alerts</b>\n\nNo alerts sent yet."

    outcome_label = {
        "OPEN":      "⏳ Open",
        "TP1":       "🎯 TP1 hit",
        "TP2":       "🎯🎯 TP2 hit",
        "TP3":       "🏆 TP3 hit",
        "SL":        "🛑 SL hit",
        "EXPIRED":   "⏸ Expired",
        "SUPERSEDED": "🔁 Replaced",
    }

    lines = [f"📋 <b>Recent Alerts</b>", f"Showing: <b>{len(signals)}</b>"]
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
            f"Entry: <code>{_fmt_alert(float(s.entry_price))}</code>\n"
            f"SL: <code>{_fmt_alert(float(s.stop_loss))}</code> | "
            f"TP: <code>{_fmt_alert(float(s.tp1))}</code> / <code>{_fmt_alert(float(s.tp2))}</code> / <code>{_fmt_alert(float(s.tp3))}</code>\n"
            f"Time: {date}"
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
