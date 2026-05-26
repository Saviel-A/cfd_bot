"""Signal profile selection by instrument."""

from datetime import datetime, timezone, timedelta

import pandas as pd

from bot.config import cfg


GOLD_SYMBOLS = {"XAUUSD", "GOLD"}


def is_gold(symbol: str) -> bool:
    return symbol.upper() in GOLD_SYMBOLS


def signal_profile(symbol: str) -> dict:
    """Return timeframe/cooldown settings for an instrument."""
    if is_gold(symbol):
        return {
            "entry_timeframe": "15m",
            "entry_label": "15M",
            "htf_timeframe": "1h",
            "htf_label": "1H",
            "duplicate_cooldown_minutes": 60,
        }
    return {
        "entry_timeframe": cfg.DEFAULT_TIMEFRAME,
        "entry_label": cfg.DEFAULT_TIMEFRAME.upper(),
        "htf_timeframe": cfg.HTF_TIMEFRAME,
        "htf_label": cfg.HTF_TIMEFRAME.upper(),
        "duplicate_cooldown_minutes": 240,
    }


def timeframe_delta(timeframe: str) -> timedelta:
    if timeframe.endswith("m"):
        return timedelta(minutes=int(timeframe[:-1]))
    if timeframe.endswith("h"):
        return timedelta(hours=int(timeframe[:-1]))
    if timeframe.endswith("d"):
        return timedelta(days=int(timeframe[:-1]))
    return timedelta(hours=1)


def stale_candle_reason(df: pd.DataFrame, timeframe: str, symbol: str) -> str | None:
    """Return a user-facing reason when the candle feed is too old."""
    if df is None or df.empty:
        return f"{symbol} candle feed returned no data"

    last = pd.Timestamp(df.index[-1])
    if last.tzinfo is None:
        last = last.tz_localize(timezone.utc)
    else:
        last = last.tz_convert(timezone.utc)

    # Allow delayed vendors, but do not trade candles from old sessions.
    max_age = timeframe_delta(timeframe) * 4
    age = datetime.now(timezone.utc) - last.to_pydatetime()
    if age > max_age:
        return f"{symbol} candle feed is stale. Last candle: {last.strftime('%d %b %H:%M UTC')}"
    return None
