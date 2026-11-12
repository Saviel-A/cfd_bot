"""Candle feed sanity checks.

A signal computed on a broken feed is worse than no signal. This runs
before the engine's verdict is trusted and returns a reason when the
most recent candles cannot be relied on.
"""

import pandas as pd

RECENT = 30            # candles inspected
FLAT_RUN = 6           # identical closes in a row = frozen feed
SPIKE_RANGE_MULT = 12  # one candle this many times the median range = bad tick


def ohlcv_quality_issue(df: pd.DataFrame) -> str | None:
    """Return a short description of the problem, or None when the feed is usable."""
    if df is None or df.empty:
        return "no candles"

    cols = ["open", "high", "low", "close"]
    if any(c not in df.columns for c in cols):
        return "missing OHLC columns"

    recent = df[cols].tail(RECENT)
    if len(recent) < FLAT_RUN:
        return "too few candles"

    if recent.isna().any().any():
        return "missing prices in recent candles"
    if (recent <= 0).any().any():
        return "non-positive price in recent candles"

    body_hi = recent[["open", "close"]].max(axis=1)
    body_lo = recent[["open", "close"]].min(axis=1)
    if (recent["high"] < body_hi).any() or (recent["low"] > body_lo).any():
        return "inconsistent candle (high/low inside the body)"

    index = df.index[-RECENT:]
    if index.has_duplicates:
        return "duplicate candle timestamps"
    if not index.is_monotonic_increasing:
        return "candles out of order"

    if recent["close"].tail(FLAT_RUN).nunique() == 1:
        return f"price frozen for {FLAT_RUN} candles"

    ranges = recent["high"] - recent["low"]
    median_range = float(ranges.iloc[:-1].median())
    if median_range > 0 and float(ranges.iloc[-1]) > SPIKE_RANGE_MULT * median_range:
        return "last candle range looks like a bad tick"

    return None
