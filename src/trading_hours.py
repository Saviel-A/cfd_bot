"""
Trading hours — per-category market sessions, displayed in Israel time (Asia/Jerusalem).

Israel uses UTC+2 (IST) in winter, UTC+3 (IDT) in summer.
The zoneinfo module handles DST automatically.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IL = ZoneInfo("Asia/Jerusalem")

# Each session: (open_hour, open_min, close_hour, close_min) in UTC, days = 0=Mon…6=Sun
# None means always open on that day.

SESSIONS = {
    "Metals":        {"type": "forex",     "label": "24/5  Sun–Fri"},
    "Energy":        {"type": "forex",     "label": "24/5  Sun–Fri"},
    "Major Forex":   {"type": "forex",     "label": "24/5  Sun–Fri"},
    "Minor Forex":   {"type": "forex",     "label": "24/5  Sun–Fri"},
    "Crypto":        {"type": "always",    "label": "24/7"},
    "US Indices":    {"type": "us",        "label": "Mon–Fri  09:30–16:00 ET"},
    "Stocks":        {"type": "us",        "label": "Mon–Fri  09:30–16:00 ET"},
    "EU Indices":    {"type": "eu",        "label": "Mon–Fri  09:00–17:30 CET"},
    "Asian Indices": {"type": "asia",      "label": "Mon–Fri  09:00–15:30 local"},
}

# UTC times for each session type
_SESSION_UTC = {
    "forex":  {"open": (22, 0, 6), "close": (22, 0, 4),   "weekdays": (0, 1, 2, 3, 4, 6)},
    "always": None,
    "us":     {"open": (13, 30),   "close": (20, 0),       "weekdays": (0, 1, 2, 3, 4)},
    "eu":     {"open": (7, 0),     "close": (15, 30),      "weekdays": (0, 1, 2, 3, 4)},
    "asia":   {"open": (0, 0),     "close": (6, 0),        "weekdays": (0, 1, 2, 3, 4)},
}


def _is_open(session_type: str, now_utc: datetime) -> bool:
    if session_type == "always":
        return True

    weekday = now_utc.weekday()  # 0=Mon, 6=Sun

    if session_type == "forex":
        # Open Sunday 22:00 UTC through Friday 22:00 UTC
        # Closed Saturday all day and Friday after 22:00
        if weekday == 5:  # Saturday
            return False
        if weekday == 6:  # Sunday
            return now_utc.hour >= 22
        if weekday == 4:  # Friday
            return now_utc.hour < 22
        return True  # Mon–Thu always open

    cfg = _SESSION_UTC.get(session_type)
    if not cfg or weekday not in cfg["weekdays"]:
        return False

    open_h, open_m   = cfg["open"]
    close_h, close_m = cfg["close"]
    t = now_utc.hour * 60 + now_utc.minute
    return open_h * 60 + open_m <= t < close_h * 60 + close_m


def _utc_to_il(hour: int, minute: int) -> str:
    """Convert UTC hour:minute to Israel local time string."""
    now = datetime.now(IL)
    offset_hours = int(now.utcoffset().total_seconds() // 3600)
    il_hour = (hour + offset_hours) % 24
    return f"{il_hour:02d}:{minute:02d}"


def get_hours_message() -> str:
    now_utc = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
    now_il  = datetime.now(IL)
    offset  = int(now_il.utcoffset().total_seconds() // 3600)
    tz_name = "IDT (UTC+3)" if offset == 3 else "IST (UTC+2)"

    lines = [
        f"🕐 <b>Market Hours</b>  —  Israel Time ({tz_name})",
        "",
    ]

    categories_display = [
        ("Metals",        "🥇 Metals & Precious"),
        ("Energy",        "🛢 Energy & Oil"),
        ("Major Forex",   "💱 Forex"),
        ("Crypto",        "₿ Crypto"),
        ("US Indices",    "🇺🇸 US Indices & Stocks"),
        ("EU Indices",    "🇪🇺 European Indices"),
        ("Asian Indices", "🌏 Asian Indices"),
    ]

    for key, display_name in categories_display:
        session = SESSIONS.get(key, {})
        stype   = session.get("type", "forex")
        is_open = _is_open(stype, now_utc)
        status  = "🟢 Open" if is_open else "🔴 Closed"

        if stype == "always":
            hours_il = "Always open"
        elif stype == "forex":
            hours_il = f"Sun {_utc_to_il(22, 0)} — Fri {_utc_to_il(22, 0)}"
        elif stype == "us":
            hours_il = f"Mon–Fri  {_utc_to_il(13, 30)} – {_utc_to_il(20, 0)}"
        elif stype == "eu":
            hours_il = f"Mon–Fri  {_utc_to_il(7, 0)} – {_utc_to_il(15, 30)}"
        elif stype == "asia":
            hours_il = f"Mon–Fri  {_utc_to_il(0, 0)} – {_utc_to_il(6, 0)}"
        else:
            hours_il = "—"

        lines.append(f"{status}  <b>{display_name}</b>")
        lines.append(f"         {hours_il}")
        lines.append("")

    lines.append(f"<i>Current Israel time: {now_il.strftime('%H:%M  %a %d %b')}</i>")
    return "\n".join(lines)
