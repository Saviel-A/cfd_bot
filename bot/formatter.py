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
    return reason[:1].upper() + reason[1:]


def _news_line(news_risk: str, news_events: Optional[list]) -> str:
    if news_risk == "WARN" and news_events:
        titles = ", ".join(e["title"] for e in news_events[:2])
        return f"News: <b>Watch</b> ({titles})"
    return "News: <b>Clear</b>"


def _pressure_line(market_pressure: Optional[dict]) -> str:
    if not market_pressure:
        return "Pressure: <b>Not checked</b>"
    direction = market_pressure.get("direction", "MIXED").title()
    buy_pct = market_pressure.get("buy_pct", 50)
    sell_pct = market_pressure.get("sell_pct", 50)
    if direction == "Buyers":
        return f"Pressure: <b>Buyers {buy_pct:.0f}%</b>"
    if direction == "Sellers":
        return f"Pressure: <b>Sellers {sell_pct:.0f}%</b>"
    return f"Pressure: <b>Mixed</b> ({buy_pct:.0f}% buy / {sell_pct:.0f}% sell)"


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
    risk_note = "Risk to SL"

    return (
        f"{arrow} <b>{name} {label}</b>\n"
        f"\n"
        f"🟢 <b>Status:</b> Active\n"
        f"⏱ <b>Timeframe:</b> 1H entry + 4H trend\n"
        f"📰 {news}\n"
        f"⚖️ {pressure}\n"
        f"\n"
        f"📌 <b>Trade Plan</b>\n"
        f"💰 Entry: {entry}\n"
        f"🛑 Stop Loss: {sl}\n"
        f"🎯 TP1: {tp1}  partial\n"
        f"🎯 TP2: {tp2}  main\n"
        f"🎯 TP3: {tp3}  runner\n"
        f"\n"
        f"⚠️ <b>Risk</b>\n"
        f"{risk_note}: {risk}\n"
        f"This is the price gap between Entry and Stop Loss.\n"
        f"If SL is hit, close the trade.\n"
        f"\n"
        f"🧠 <b>Setup</b>\n"
        f"4H trend: <b>{signal.htf_bias.title()}</b>\n"
        f"EMA: {ema}  MACD: {macd}  RSI: {rsi}\n"
        f"\n"
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
        f"<b>Status</b>\n"
        f"{status}\n"
        f"Last price: {price_str}\n\n"
        "No signal is sent while this market is closed."
    )


# Hold card
def format_hold_message(display_name: str, signal: Signal, symbol: str = "") -> str:
    is_open, mkt = symbol_market_status(symbol or display_name)
    reason = signal.reason or "No clean setup"
    name = get_symbol_label(symbol) if symbol else display_name
    return (
        f"⚪ <b>{name}</b>\n"
        f"\n"
        f"<b>Status</b>\n"
        f"{mkt}\n\n"
        f"<b>Decision</b>\n"
        f"No signal right now.\n\n"
        f"<b>Reason</b>\n"
        f"{_format_reason(reason)}"
    )


# Watchlist
def format_watchlist_message(results: list) -> str:
    if not results:
        return "📋 <b>Watchlist</b>\n\nNo symbols are tracked yet.\nUse /add to start scanning."

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
            badge = "📈 Signal: BUY"
            reason = "Setup is aligned"
        elif signal.direction == "SELL":
            badge = "📉 Signal: SELL"
            reason = "Setup is aligned"
        else:
            badge = "⚪ Signal: None"
            reason = _format_reason(signal.reason or "No clean setup")

        lines.append(
            f"{dot} <b>{name}</b>\n"
            f"Price: {price}\n"
            f"{badge}\n"
            f"Reason: {reason}\n"
        )

    return "\n".join(lines)


def format_stats_message(stats: dict) -> str:
    if not stats["total"]:
        return "📊 <b>Stats</b>\n\nNo signals yet. Run the bot, collect outcomes, then use /stats."

    closed = stats["closed"]
    win_rate = (stats["wins"] / closed * 100) if closed else 0

    lines = [
        f"📊 <b>Performance Stats</b>",
        f"Last checked signals: <b>{stats['total']}</b>",
        "",
        "<b>Summary</b>",
        f"Open trades: <b>{stats['open']}</b>",
        f"Closed trades: <b>{closed}</b>",
        f"Winning trades: <b>{stats['wins']}</b>",
        f"Losing trades: <b>{stats['losses']}</b>",
        f"Expired trades: <b>{stats['expired']}</b>",
        f"Win rate: <b>{win_rate:.1f}%</b>",
        "",
        "<b>Targets Hit</b>",
        f"TP1 hit: <b>{stats['tp1']}</b>",
        f"TP2 hit: <b>{stats['tp2']}</b>",
        f"TP3 hit: <b>{stats['tp3']}</b>",
    ]

    if stats["best_symbols"]:
        lines.extend(["", "<b>Best Symbols</b>"])
        for s in stats["best_symbols"]:
            lines.append(
                f"{s['symbol']}: <b>{s['wins']}</b> wins from <b>{s['total']}</b> closed trades"
            )

    if stats["worst_symbols"]:
        worst_symbols = [s for s in stats["worst_symbols"] if s["losses"]]
        if worst_symbols:
            lines.extend(["", "<b>Needs Review</b>"])
            for s in worst_symbols:
                lines.append(
                    f"{s['symbol']}: <b>{s['losses']}</b> losses from <b>{s['total']}</b> closed trades"
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

    lines = [f"📋 <b>Signal History</b>", f"Showing: <b>{len(signals)}</b>", ""]
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
            f"{dot} <b>{s.symbol} {s.direction}</b>\n"
            f"Status: {outcome}\n"
            f"Entry: <code>{_fmt_alert(float(s.entry_price))}</code>\n"
            f"Time: {date}\n"
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
        "📡 <b>Scan Complete</b>",
        "",
        f"Symbols checked: <b>{checked}</b>",
        f"Updates sent to channel: <b>{broadcasts}</b>",
    ]

    if broadcasts:
        lines.extend(["", "Channel was updated successfully."])
    else:
        lines.extend(["", "<b>Why no signal was sent</b>"])
        if holds:
            for item in holds[:8]:
                if ":" in item:
                    symbol, reason = item.split(":", 1)
                    lines.append(f"{symbol.strip()}: {_format_reason(reason.strip())}")
                else:
                    lines.append(_format_reason(item))
        else:
            lines.append("No clean setup matched the strategy rules.")

    if errors:
        lines.extend(["", "<b>Errors</b>"])
        lines.extend(errors[:5])

    return "\n".join(lines)
