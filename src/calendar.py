"""
Economic calendar — pulls from Forex Factory's public JSON feed.
Shows today's high/medium impact events in Israel time.
"""

import logging
import requests
from datetime import datetime, date
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
    now_il  = datetime.now(IL)
    tz      = f"UTC+{int(now_il.utcoffset().total_seconds()//3600)}"
    period  = "Today" if today_only else "This Week"

    if not events:
        return (
            f"📅 <b>Economic Calendar — {period}</b>\n\n"
            "No high or medium impact events today.\n\n"
            "<i>Use /calendar week to see the full week.</i>"
        )

    lines = [f"📅 <b>Economic Calendar — {period}</b>  ({tz})", ""]

    current_time = now_il.strftime("%H:%M")
    for ev in events:
        icon     = _IMPACT_ICON.get(ev["impact"], "⚪")
        past     = "~" if ev["time_il"] < current_time else ""
        forecast = f"  F: <b>{ev['forecast']}</b>" if ev["forecast"] else ""
        prev     = f"  P: {ev['previous']}" if ev["previous"] else ""

        lines.append(
            f"{past}{icon} <b>{ev['time_il']}</b>  {ev['currency']}  {ev['title']}"
            f"{forecast}{prev}"
        )

    lines.append("")
    lines.append(f"🔴 High impact   🟡 Medium impact   ~ Past")
    lines.append(f"<i>{now_il.strftime('%a %d %b · %H:%M')} (Israel)</i>")
    return "\n".join(lines)
