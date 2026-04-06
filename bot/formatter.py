"""Telegram message formatter."""

from src.signal_engine import Signal
from src.risk_manager import TradeParams
from typing import Optional

DIV = "━━━━━━━━━━━━━━━━━━━━"


def _fmt(price: float) -> str:
    if price < 10:
        return f"{price:.5f}"
    return f"{price:,.2f}"


def format_signal_message(display_name: str, signal: Signal, trade: Optional[TradeParams]) -> str:
    is_buy = signal.direction == "BUY"
    header = f"🟢 <b>{display_name}  —  BUY</b>" if is_buy else f"🔴 <b>{display_name}  —  SELL</b>"

    lines = [
        header,
        DIV,
        f"💰  Entry           <code>{_fmt(signal.current_price)}</code>",
        f"🛑  Stop Loss       <code>{_fmt(trade.stop_loss) if trade else '—'}</code>",
        DIV,
        f"🎯  TP 1             <code>{_fmt(trade.tp1) if trade else '—'}</code>",
        f"🎯  TP 2             <code>{_fmt(trade.tp2) if trade else '—'}</code>",
        f"🎯  TP 3             <code>{_fmt(trade.tp3) if trade else '—'}</code>",
        DIV,
        "<i>Not financial advice. Manage your risk.</i>",
    ]
    return "\n".join(lines)


def format_hold_message(display_name: str, signal: Signal) -> str:
    return "\n".join([
        f"<b>{display_name}</b>",
        f"Confluence    {signal.strength} of {signal.total_indicators}",
        "",
        "<i>No signal at this time.</i>",
    ])


def format_watchlist_message(results: list) -> str:
    if not results:
        return "Your watchlist is empty.\n\nUse /add XAUUSD to add a symbol."

    lines = ["<b>Watchlist</b>"]
    for r in results:
        if r.get("error"):
            lines.append(f"\n<b>{r['symbol']}</b>\nunavailable")
            continue
        signal = r["signal"]
        price = _fmt(signal.current_price) if signal.current_price else "—"
        if signal.direction == "BUY":
            badge = "🟢 BUY"
        elif signal.direction == "SELL":
            badge = "🔴 SELL"
        else:
            badge = "⚪ HOLD"
        lines.append(f"\n<b>{r['display_name']}</b>\n{badge}   <code>{price}</code>")

    return "\n".join(lines)


def format_settings_message(s, watchlist_count: int) -> str:
    alerts = "ON" if s.alerts_enabled else "OFF"
    return "\n".join([
        "<b>Your Settings</b>",
        "",
        f"Alerts          <b>{alerts}</b>",
        f"Timeframe       <b>{str(s.timeframe).upper()}</b>",
        f"Confluence      <b>{s.min_confluence} of 4</b>",
        f"Balance         <b>${float(s.account_balance):,.0f}</b>",
        f"Risk per trade  <b>{float(s.risk_percent)}%</b>",
        f"Watchlist       <b>{watchlist_count} symbols</b>",
        "",
        "/alerts on|off",
        "/timeframe 1h",
        "/confluence 3",
        "/balance 10000",
        "/risk 1.5",
    ])


def format_history_message(signals: list) -> str:
    if not signals:
        return "No signal history yet."

    outcome_icon = {
        "OPEN": "⏳", "TP1": "🎯", "TP2": "🎯",
        "TP3": "🎯", "SL": "❌", "EXPIRED": "⏸",
    }
    lines = ["<b>Signal History</b>"]
    for s in signals:
        direction_icon = "🟢" if s.direction == "BUY" else "🔴"
        icon = outcome_icon.get(s.outcome, "")
        date = str(s.fired_at)[:10]
        entry = _fmt(float(s.entry_price))
        lines.append(
            f"\n{direction_icon} <b>{s.symbol}</b>  {s.direction}\n"
            f"Entry <code>{entry}</code>   {icon} {s.outcome}   <i>{date}</i>"
        )
    return "\n".join(lines)


def format_performance_message(stats: dict) -> str:
    if stats["total"] == 0:
        return (
            "<b>Performance</b>\n\n"
            "No resolved signals yet.\n\n"
            "<i>Stats update automatically as signals hit TP or SL.</i>"
        )

    win_rate = stats["win_rate"]
    filled = round(win_rate / 10)
    bar = "🟩" * filled + "⬛" * (10 - filled)

    return "\n".join([
        "<b>Performance</b>",
        "",
        bar,
        f"Win Rate   <b>{win_rate}%</b>",
        "",
        f"Wins       <b>{stats['wins']}</b>",
        f"Losses     <b>{stats['losses']}</b>",
        f"Total      <b>{stats['total']}</b>",
        "",
        f"TP 1       <b>{stats['tp1']}</b>",
        f"TP 2       <b>{stats['tp2']}</b>",
        f"TP 3       <b>{stats['tp3']}</b>",
        f"SL         <b>{stats['sl']}</b>",
        "",
        "<i>Tracked automatically.</i>",
    ])
