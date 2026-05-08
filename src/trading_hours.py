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
    # Use UTC for non-forex markets
    wd_utc = now.weekday()
    h_utc  = now.hour
    t_utc  = h_utc * 60 + now.minute

    if market == "crypto":
        return True

    if market == "forex":
        # Use Israel local time — DST-aware, matches broker schedule
        now_il = now.astimezone(IL)
        wd = now_il.weekday()   # 0=Mon … 6=Sun
        h  = now_il.hour

        if wd == 5:             # Saturday: always closed
            return False
        if wd == 6:             # Sunday: opens at 23:00 Israel
            return h >= 23
        if h == 23:             # Daily rollover break 23:00–00:00 every night
            return False
        # Mon–Fri 00:00–22:59: open
        return True

    if market == "us":
        # NYSE: Mon–Fri 13:30–20:00 UTC
        return wd_utc <= 4 and 13 * 60 + 30 <= t_utc < 20 * 60

    if market == "eu":
        # Xetra/LSE: Mon–Fri 07:00–15:30 UTC
        return wd_utc <= 4 and 7 * 60 <= t_utc < 15 * 60 + 30

    if market == "asia":
        # TSE/HSI: Mon–Fri 00:00–06:00 UTC
        return wd_utc <= 4 and t_utc < 6 * 60

    return False


# Map every symbol to its market type
_SYMBOL_MARKET: dict[str, str] = {}

_CATEGORY_MARKET = {
    "Metals":        "forex",
    "Energy":        "forex",
    "Major Forex":   "forex",
    "Minor Forex":   "forex",
    "Crypto":        "crypto",
    "US Indices":    "us",
    "Stocks":        "us",
    "EU Indices":    "eu",
    "Asian Indices": "asia",
}


def _build_symbol_map():
    """Lazily build symbol → market type from CATEGORIES."""
    if _SYMBOL_MARKET:
        return
    from src.instruments import CATEGORIES
    for cat, symbols in CATEGORIES.items():
        mtype = _CATEGORY_MARKET.get(cat, "forex")
        for s in symbols:
            _SYMBOL_MARKET[s] = mtype


def symbol_market_status(symbol: str) -> tuple[bool, str]:
    """Return (is_open, label) for a symbol right now."""
    _build_symbol_map()
    mtype = _SYMBOL_MARKET.get(symbol.upper(), "forex")
    now_il = datetime.now(IL)
    open_ = _is_open(mtype, _now_utc())
    label = (
        f"🟢 Market Open. Israel time: {now_il.strftime('%H:%M')}"
        if open_
        else f"🔴 Market Closed. Israel time: {now_il.strftime('%H:%M')}"
    )
    return open_, label


def get_hours_message() -> str:
    now_utc = _now_utc()
    now_il  = datetime.now(IL)
    offset  = int(now_il.utcoffset().total_seconds() // 3600)
    tz      = f"UTC+{offset}"

    markets = [
        ("forex",  "💱",  "Metals, Forex and Energy",
            "Mon 00:00  Fri 23:00\n"
            "        Daily break: 23:00  00:00\n"
            "        Sat Sun: closed until Sun 23:00"),
        ("crypto", "₿",   "Crypto",
            "24/7, no break"),
        ("us",     "🇺🇸", "US Indices and Stocks",
            f"Mon  Fri  {_to_il(13,30)}  {_to_il(20,0)}"),
        ("eu",     "🇪🇺", "European Indices",
            f"Mon  Fri  {_to_il(7,0)}  {_to_il(15,30)}"),
        ("asia",   "🌏",  "Asian Indices",
            f"Mon  Fri  {_to_il(0,0)}  {_to_il(6,0)}"),
    ]

    lines = [f"🕐 <b>Market Hours</b>  (Israel time, {tz})", ""]

    for key, icon, name, hours in markets:
        dot = "🟢" if _is_open(key, now_utc) else "🔴"
        lines.append(f"{dot} {icon} <b>{name}</b>\n        {hours}")

    lines.append("")
    lines.append(now_il.strftime("%A, %d %B  %H:%M Israel time"))
    return "\n".join(lines)
