"""
Trading hours — per-category market sessions in Israel time.

ZoneInfo("Asia/Jerusalem") handles IST/IDT (DST) automatically.
No manual offset calculation needed.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IL  = ZoneInfo("Asia/Jerusalem")
UTC = ZoneInfo("UTC")


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _to_il(hour: int, minute: int) -> str:
    """Convert a UTC hour:minute to Israel local time string (DST-aware)."""
    now_il = datetime.now(IL)
    offset_h = int(now_il.utcoffset().total_seconds() // 3600)
    il_h = (hour + offset_h) % 24
    return f"{il_h:02d}:{minute:02d}"


def _is_open(market: str, now: datetime) -> bool:
    wd = now.weekday()  # 0=Mon … 6=Sun
    h  = now.hour
    m  = now.minute
    t  = h * 60 + m

    if market == "crypto":
        return True

    if market == "forex":
        # Open Sun 22:00 UTC → Fri 22:00 UTC; closed Sat all day
        if wd == 5:                          # Saturday — closed
            return False
        if wd == 6:                          # Sunday — open from 22:00
            return t >= 22 * 60
        if wd == 4:                          # Friday — closes at 22:00
            return t < 22 * 60
        return True                          # Mon–Thu always open

    if market == "us":
        # NYSE: Mon–Fri 13:30–20:00 UTC
        return wd <= 4 and 13 * 60 + 30 <= t < 20 * 60

    if market == "eu":
        # Xetra/LSE: Mon–Fri 07:00–15:30 UTC
        return wd <= 4 and 7 * 60 <= t < 15 * 60 + 30

    if market == "asia":
        # TSE/HSI: Mon–Fri 00:00–06:00 UTC
        return wd <= 4 and t < 6 * 60

    return False


def get_hours_message() -> str:
    now_utc = _now_utc()
    now_il  = datetime.now(IL)
    offset  = int(now_il.utcoffset().total_seconds() // 3600)
    tz      = f"UTC+{offset}"

    markets = [
        ("forex",  "💱",  "Metals, Forex & Energy",  f"Sun {_to_il(22,0)} – Fri {_to_il(22,0)}"),
        ("crypto", "₿",   "Crypto",                   "Always open"),
        ("us",     "🇺🇸", "US Indices & Stocks",      f"Mon–Fri  {_to_il(13,30)} – {_to_il(20,0)}"),
        ("eu",     "🇪🇺", "European Indices",         f"Mon–Fri  {_to_il(7,0)} – {_to_il(15,30)}"),
        ("asia",   "🌏",  "Asian Indices",             f"Mon–Fri  {_to_il(0,0)} – {_to_il(6,0)}"),
    ]

    lines = [f"🕐 <b>Market Hours</b>  ·  Israel Time ({tz})", ""]

    for key, icon, name, hours in markets:
        dot = "🟢" if _is_open(key, now_utc) else "🔴"
        lines.append(f"{dot} {icon} <b>{name}</b>")
        lines.append(f"    {hours}")
        lines.append("")

    lines.append(f"<i>{now_il.strftime('%a %d %b · %H:%M')} (Israel)</i>")
    return "\n".join(lines)
