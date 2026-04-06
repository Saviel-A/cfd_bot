"""Telegram message formatter."""

from src.signal_engine import Signal
from src.risk_manager import TradeParams
from typing import Optional

DIV = "━━━━━━━━━━━━━━━━━━━━"


def _fmt(price: float) -> str:
    if price < 10:
        return f"{price:.5f}"
    return f"{price:,.2f}"


# ── Signal card ───────────────────────────────────────────────────────────────

def format_signal_message(display_name: str, signal: Signal, trade: Optional[TradeParams]) -> str:
    is_buy = signal.direction == "BUY"
    header = (
        f"🟢 <b>{display_name}</b>  —  <b>BUY</b>"
        if is_buy else
        f"🔴 <b>{display_name}</b>  —  <b>SELL</b>"
    )

    # Indicator votes
    vote_parts = []
    for name, val in (signal.details or {}).items():
        icon = "✅" if val == 1 else ("❌" if val == -1 else "➖")
        vote_parts.append(f"{icon} {name}")
    votes_line = "  ".join(vote_parts) if vote_parts else ""

    bias_icon = {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}.get(signal.htf_bias, "➡️")

    lines = [
        header,
        DIV,
        f"💰 Entry       <code>{_fmt(signal.current_price)}</code>",
        f"🛑 Stop Loss   <code>{_fmt(trade.stop_loss) if trade else '—'}</code>",
        DIV,
        f"🎯 TP 1        <code>{_fmt(trade.tp1) if trade else '—'}</code>",
        f"🎯 TP 2        <code>{_fmt(trade.tp2) if trade else '—'}</code>",
        f"🎯 TP 3        <code>{_fmt(trade.tp3) if trade else '—'}</code>",
        DIV,
    ]
    if votes_line:
        lines.append(votes_line)
        lines.append(f"{bias_icon} HTF Trend - <b>{signal.htf_bias}</b>")
        lines.append("")
    lines.append("<i>Not financial advice. Manage your risk.</i>")
    return "\n".join(lines)


# ── Hold card ─────────────────────────────────────────────────────────────────

def format_hold_message(display_name: str, signal: Signal) -> str:
    bias_icon = {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}.get(signal.htf_bias, "➡️")

    vote_parts = []
    for name, val in (signal.details or {}).items():
        icon = "✅" if val == 1 else ("❌" if val == -1 else "➖")
        vote_parts.append(f"{icon} {name}")
    votes_line = "  ".join(vote_parts) if vote_parts else "—"

    bar = "🟦" * signal.strength + "⬛" * (signal.total_indicators - signal.strength)

    return "\n".join([
        f"⚪ <b>{display_name}</b>  —  No Signal",
        DIV,
        f"{bar}  {signal.strength}/{signal.total_indicators} indicators",
        "",
        votes_line,
        "",
        f"{bias_icon} HTF Trend - <b>{signal.htf_bias}</b>",
        DIV,
        "<i>No signal at this time.</i>",
    ])


# ── Watchlist ─────────────────────────────────────────────────────────────────

def format_watchlist_message(results: list) -> str:
    if not results:
        return (
            "📋 <b>Watchlist</b>\n\n"
            "Your watchlist is empty.\n\n"
            "Use /add XAUUSD to start tracking a symbol."
        )

    lines = [f"📋 <b>Watchlist</b>  ({len(results)} symbols)", DIV]
    for r in results:
        if r.get("error"):
            lines.append(f"⚠️ <b>{r['display_name']}</b>  —  <i>unavailable</i>")
            continue
        signal = r["signal"]
        price = f"<code>{_fmt(signal.current_price)}</code>" if signal.current_price else "<code>—</code>"
        if signal.direction == "BUY":
            badge = "🟢 <b>BUY</b>"
        elif signal.direction == "SELL":
            badge = "🔴 <b>SELL</b>"
        else:
            badge = f"⚪ HOLD  <i>({signal.strength}/{signal.total_indicators})</i>"
        lines.append(f"{badge}  <b>{r['display_name']}</b>  {price}")

    return "\n".join(lines)


# ── Settings ──────────────────────────────────────────────────────────────────

def format_settings_message(s, watchlist_count: int) -> str:
    alerts_icon = "🔔" if s.alerts_enabled else "🔕"
    alerts_text = "ON" if s.alerts_enabled else "OFF"
    return "\n".join([
        "⚙️ <b>Your Settings</b>",
        DIV,
        f"{alerts_icon} Alerts        <b>{alerts_text}</b>",
        f"⏱ Timeframe     <b>{str(s.timeframe).upper()}</b>",
        f"🎯 Confluence    <b>{s.min_confluence} of 4</b>",
        f"📋 Watchlist     <b>{watchlist_count} symbols</b>",
        "",
        f"💰 Balance       <b>${float(s.account_balance):,.0f}</b>",
        f"⚡ Risk/trade    <b>{float(s.risk_percent)}%</b>",
        DIV,
        "<i>/alerts  /timeframe  /confluence  /balance  /risk</i>",
    ])


# ── Signal history ────────────────────────────────────────────────────────────

def format_history_message(signals: list) -> str:
    if not signals:
        return (
            "📜 <b>Signal History</b>\n\n"
            "No signals yet.\n\n"
            "<i>History builds as signals are generated for you.</i>"
        )

    outcome_icon = {
        "OPEN": "⏳ Open",
        "TP1":  "🎯 TP1",
        "TP2":  "🎯 TP2",
        "TP3":  "🎯 TP3",
        "SL":   "❌ SL Hit",
        "EXPIRED": "⏸ Expired",
    }

    lines = [f"📜 <b>Signal History</b>", DIV]
    for s in signals:
        dir_icon = "🟢" if s.direction == "BUY" else "🔴"
        outcome  = outcome_icon.get(s.outcome, s.outcome)
        date     = str(s.fired_at)[:10]
        entry    = _fmt(float(s.entry_price))
        lines.append(
            f"{dir_icon} <b>{s.symbol}</b>  {s.direction}  —  {outcome}\n"
            f"   Entry <code>{entry}</code>  ·  <i>{date}</i>"
        )
    return "\n".join(lines)


# ── Performance ───────────────────────────────────────────────────────────────

def format_performance_message(stats: dict) -> str:
    if stats["total"] == 0:
        return (
            "📊 <b>Performance</b>\n"
            + DIV + "\n"
            "No resolved signals yet.\n\n"
            "<i>Stats update automatically as signals hit TP or SL.</i>"
        )

    win_rate = stats["win_rate"]
    filled   = round(win_rate / 10)
    bar      = "🟩" * filled + "⬛" * (10 - filled)

    return "\n".join([
        "📊 <b>Performance</b>",
        DIV,
        f"{bar}",
        f"Win Rate   <b>{win_rate}%</b>",
        "",
        f"✅ Wins      <b>{stats['wins']}</b>",
        f"❌ Losses    <b>{stats['losses']}</b>",
        f"📈 Total     <b>{stats['total']}</b>",
        "",
        f"🎯 TP1       <b>{stats['tp1']}</b>",
        f"🎯 TP2       <b>{stats['tp2']}</b>",
        f"🎯 TP3       <b>{stats['tp3']}</b>",
        f"🛑 SL        <b>{stats['sl']}</b>",
        DIV,
        "<i>Tracked automatically.</i>",
    ])
