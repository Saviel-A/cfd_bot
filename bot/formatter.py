"""Telegram message formatter — no divider lines anywhere."""

from src.signal_engine import Signal
from src.risk_manager import TradeParams
from src.trading_hours import symbol_market_status
from typing import Optional


def _fmt(price: float) -> str:
    if price < 10:
        return f"{price:.5f}"
    return f"{price:,.2f}"


# ── Signal card ───────────────────────────────────────────────────────────────

def format_signal_message(display_name: str, signal: Signal, trade: Optional[TradeParams], symbol: str = "") -> str:
    is_buy = signal.direction == "BUY"
    header = f"🟢 <b>{display_name}  BUY</b>" if is_buy else f"🔴 <b>{display_name}  SELL</b>"

    vote_parts = []
    for name, val in (signal.details or {}).items():
        icon = "✅" if val == 1 else ("❌" if val == -1 else "➖")
        vote_parts.append(f"{icon} {name}")

    bias_icon = {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}.get(signal.htf_bias, "➡️")
    _, mkt = symbol_market_status(symbol or display_name)

    return "\n".join([
        header,
        "",
        f"💰 Entry      <code>{_fmt(signal.current_price)}</code>",
        f"🛑 Stop Loss  <code>{_fmt(trade.stop_loss) if trade else '—'}</code>",
        "",
        f"🎯 TP 1  <code>{_fmt(trade.tp1) if trade else '—'}</code>",
        f"🎯 TP 2  <code>{_fmt(trade.tp2) if trade else '—'}</code>",
        f"🎯 TP 3  <code>{_fmt(trade.tp3) if trade else '—'}</code>",
        "",
        "  ".join(vote_parts),
        f"{bias_icon} Trend  <b>{signal.htf_bias}</b>    {mkt}",
        "",
        "<i>Not financial advice. Manage your risk.</i>",
    ])


# ── Hold card ─────────────────────────────────────────────────────────────────

def format_hold_message(display_name: str, signal: Signal, symbol: str = "") -> str:
    bias_icon = {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}.get(signal.htf_bias, "➡️")
    _, mkt = symbol_market_status(symbol or display_name)

    vote_parts = []
    for name, val in (signal.details or {}).items():
        icon = "✅" if val == 1 else ("❌" if val == -1 else "➖")
        vote_parts.append(f"{icon} {name}")

    bar = "🟦" * signal.strength + "⬛" * (signal.total_indicators - signal.strength)

    return "\n".join([
        f"⚪ <b>{display_name}</b>",
        "",
        f"{bar}  {signal.strength} of {signal.total_indicators} indicators",
        "  ".join(vote_parts),
        f"{bias_icon} Trend  <b>{signal.htf_bias}</b>    {mkt}",
        "",
        "<i>No signal at this time.</i>",
    ])


# ── Watchlist ─────────────────────────────────────────────────────────────────

def format_watchlist_message(results: list) -> str:
    if not results:
        return "📋 <b>Watchlist is empty</b>\n\nUse /add XAUUSD to start tracking a symbol."

    lines = ["📋 <b>Watchlist</b>", ""]
    for r in results:
        if r.get("error"):
            lines.append(f"⚠️ <b>{r['display_name']}</b>  unavailable")
            continue
        signal   = r["signal"]
        symbol   = r.get("symbol", "")
        price    = f"<code>{_fmt(signal.current_price)}</code>" if signal.current_price else "<code>—</code>"
        is_open, _ = symbol_market_status(symbol)
        mkt_dot  = "🟢" if is_open else "🔴"

        if signal.direction == "BUY":
            sig_badge = "▲ BUY"
        elif signal.direction == "SELL":
            sig_badge = "▼ SELL"
        else:
            sig_badge = f"- {signal.strength}/{signal.total_indicators}"

        lines.append(f"{mkt_dot} <b>{r['display_name']}</b>  {price}  <i>{sig_badge}</i>")

    return "\n".join(lines)


# ── Settings ──────────────────────────────────────────────────────────────────

def format_settings_message(s, watchlist_count: int) -> str:
    alerts_icon = "🔔" if s.alerts_enabled else "🔕"
    alerts_text = "ON" if s.alerts_enabled else "OFF"
    return "\n".join([
        "⚙️ <b>Settings</b>",
        "",
        f"{alerts_icon} Alerts       <b>{alerts_text}</b>",
        f"⏱ Timeframe    <b>{str(s.timeframe).upper()}</b>",
        f"🎯 Confluence   <b>{s.min_confluence} of 4</b>",
        f"📋 Watchlist    <b>{watchlist_count} symbols</b>",
        f"💰 Balance      <b>${float(s.account_balance):,.0f}</b>",
        f"⚡ Risk/trade   <b>{float(s.risk_percent)}%</b>",
        "",
        "<i>Tap a button below to change any setting.</i>",
    ])


# ── Signal history ────────────────────────────────────────────────────────────

def format_history_message(signals: list) -> str:
    if not signals:
        return "📜 <b>Signal History</b>\n\nNo signals yet."

    outcome_icon = {
        "OPEN": "⏳", "TP1": "🎯 TP1", "TP2": "🎯 TP2",
        "TP3": "🎯 TP3", "SL": "❌ SL", "EXPIRED": "⏸",
    }

    lines = ["📜 <b>Signal History</b>", ""]
    for s in signals:
        dir_icon = "🟢" if s.direction == "BUY" else "🔴"
        outcome  = outcome_icon.get(s.outcome, s.outcome)
        date     = str(s.fired_at)[:10]
        entry    = _fmt(float(s.entry_price))
        lines.append(f"{dir_icon} <b>{s.symbol}</b>  {outcome}  <code>{entry}</code>  <i>{date}</i>")

    return "\n".join(lines)


# ── Performance ───────────────────────────────────────────────────────────────

def format_performance_message(stats: dict) -> str:
    if stats["total"] == 0:
        return (
            "📊 <b>Performance</b>\n\n"
            "No resolved signals yet.\n\n"
            "<i>Stats update as signals hit TP or SL.</i>"
        )

    win_rate = stats["win_rate"]
    filled   = round(win_rate / 10)
    bar      = "🟩" * filled + "⬛" * (10 - filled)

    return "\n".join([
        "📊 <b>Performance</b>",
        "",
        f"{bar}  <b>{win_rate}%</b>",
        "",
        f"✅ Wins    <b>{stats['wins']}</b>",
        f"❌ Losses  <b>{stats['losses']}</b>",
        f"📈 Total   <b>{stats['total']}</b>",
        "",
        f"🎯 TP1  <b>{stats['tp1']}</b>   TP2  <b>{stats['tp2']}</b>   TP3  <b>{stats['tp3']}</b>",
        f"🛑 SL   <b>{stats['sl']}</b>",
        "",
        "<i>Tracked automatically.</i>",
    ])
