"""
Economic calendar — pulls from Forex Factory's public JSON feed.
Shows today's high/medium impact events in Israel time.
"""

import logging
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IL  = ZoneInfo("Asia/Jerusalem")
UTC = ZoneInfo("UTC")

_FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

_IMPACT_ICON = {
    "High":   "🔴",
    "Medium": "🟡",
    "Low":    "⚪",
    "Holiday":"📅",
}

# Currencies we care about most for CFD trading
_RELEVANT = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "XAU", "OIL"}

# Which currencies to watch per symbol
_SYMBOL_CURRENCIES: dict[str, list[str]] = {
    # Metals — USD drives gold/silver
    "XAUUSD": ["USD"], "GOLD":   ["USD"],
    "XAGUSD": ["USD"], "SILVER": ["USD"],
    "XPTUSD": ["USD"], "COPPER": ["USD"],
    # Energy
    "USOIL": ["USD"], "OIL": ["USD"], "WTIUSD": ["USD"],
    "BRENT": ["USD"], "NATGAS": ["USD"],
    # US indices
    "US30":  ["USD"], "US500": ["USD"], "NAS100": ["USD"],
    "US2000": ["USD"], "VIX": ["USD"],
    # EU/UK/Asian indices
    "UK100": ["GBP"], "FTSE": ["GBP"],
    "GER40": ["EUR"], "DAX":  ["EUR"],
    "FRA40": ["EUR"], "CAC":  ["EUR"],
    "EU50":  ["EUR"],
    "JPN225": ["JPY"], "NIKKEI": ["JPY"],
    "HK50":  ["CNH"],
    "AUS200": ["AUD"],
    # Crypto — USD moves them
    "BTC": ["USD"], "BTCUSD": ["USD"],
    "ETH": ["USD"], "ETHUSD": ["USD"],
    "SOL": ["USD"], "XRP": ["USD"], "BNB": ["USD"],
}


def _parse_ff_time(raw: str) -> datetime | None:
    """Parse Forex Factory datetime string to UTC datetime."""
    if not raw:
        return None
    try:
        # FF format: "2026-04-07T12:30:00-04:00" (ET with offset)
        return datetime.fromisoformat(raw).astimezone(UTC)
    except Exception:
        return None


def get_calendar(today_only: bool = True) -> list[dict]:
    """Fetch and return economic events, filtered to high/medium impact."""
    try:
        resp = requests.get(_FF_URL, timeout=8)
        resp.raise_for_status()
        events = resp.json()
    except Exception as e:
        logger.error(f"Calendar fetch failed: {e}")
        return []

    today_il = datetime.now(IL).date()  # Israel date, DST-aware
    results  = []

    for ev in events:
        impact = ev.get("impact", "Low")
        if impact not in ("High", "Medium"):
            continue

        currency = ev.get("country", "").upper()

        dt_utc = _parse_ff_time(ev.get("date"))
        if dt_utc is None:
            continue

        dt_il = dt_utc.astimezone(IL)
        if today_only and dt_il.date() != today_il:
            continue

        results.append({
            "time_il":  dt_il.strftime("%H:%M"),
            "currency": currency,
            "title":    ev.get("title", ""),
            "impact":   impact,
            "forecast": ev.get("forecast", ""),
            "previous": ev.get("previous", ""),
        })

    results.sort(key=lambda x: x["time_il"])
    return results


def format_calendar_message(events: list, today_only: bool = True) -> str:
    now_il       = datetime.now(IL)
    tz           = f"UTC+{int(now_il.utcoffset().total_seconds()//3600)}"
    period       = "Today" if today_only else "This Week"
    current_time = now_il.strftime("%H:%M")

    if not events:
        return f"📅 <b>Economic Calendar</b>  ({period})\n\nNo high or medium impact events."

    upcoming = [e for e in events if e["time_il"] >= current_time]
    past     = [e for e in events if e["time_il"] <  current_time]

    def _fmt_event(ev: dict, done: bool) -> str:
        icon     = _IMPACT_ICON.get(ev["impact"], "⚪")
        forecast = f"\nForecast: <code>{ev['forecast']}</code>" if ev["forecast"] else ""
        prev     = f"\nPrevious: <code>{ev['previous']}</code>" if ev["previous"] else ""
        check    = "✅ " if done else ""
        return (
            f"{check}{icon} <code>{ev['time_il']}</code>  <b>{ev['currency']}</b>\n"
            f"{ev['title']}{forecast}{prev}"
        )

    lines = [f"📅 <b>Economic Calendar</b>", f"Period: <b>{period}</b>", f"Timezone: <b>Israel time ({tz})</b>", ""]

    if upcoming:
        lines.append("<b>Upcoming</b>")
        for ev in upcoming:
            lines.append(_fmt_event(ev, done=False))

    if upcoming and past:
        lines.append("")

    if past:
        lines.append("<b>Past</b>")
        for ev in past:
            lines.append(_fmt_event(ev, done=True))

    lines.append("")
    lines.append(f"🔴 High  🟡 Medium  {now_il.strftime('%d %B  %H:%M Israel time')}")
    return "\n".join(lines)


def check_news_risk(symbol: str) -> tuple[str, list[dict]]:
    """
    Check if a high-impact news event is near for this symbol's currencies.

    Returns:
        ("BLOCK", events)  — HIGH impact within 60 min  → don't send signal
        ("WARN",  events)  — HIGH impact within 120 min → send with warning
        ("CLEAR", [])      — no nearby risk
    """
    # Derive relevant currencies from symbol
    upper = symbol.upper()
    currencies = _SYMBOL_CURRENCIES.get(upper)
    if not currencies:
        # Auto-detect from symbol name (e.g. EURUSD → EUR, USD)
        currencies = []
        for cur in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"]:
            if cur in upper:
                currencies.append(cur)
        if not currencies:
            return "CLEAR", []

    try:
        resp = requests.get(_FF_URL, timeout=5)
        resp.raise_for_status()
        all_events = resp.json()
    except Exception:
        return "CLEAR", []  # if calendar unavailable, don't block

    now_utc   = datetime.now(UTC)
    block_hit = []
    warn_hit  = []

    for ev in all_events:
        if ev.get("impact") != "High":
            continue
        currency = ev.get("country", "").upper()
        if currency not in currencies:
            continue
        dt_utc = _parse_ff_time(ev.get("date"))
        if dt_utc is None:
            continue
        delta = dt_utc - now_utc
        minutes = delta.total_seconds() / 60
        # Also catch events that just passed (within last 30 min — market still reacting)
        if -30 <= minutes <= 60:
            block_hit.append({"title": ev.get("title",""), "minutes": round(minutes)})
        elif 60 < minutes <= 120:
            warn_hit.append({"title": ev.get("title",""), "minutes": round(minutes)})

    if block_hit:
        return "BLOCK", block_hit
    if warn_hit:
        return "WARN", warn_hit
    return "CLEAR", []
