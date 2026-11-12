"""Account-level risk guards that sit above the signal engine.

The engine judges one chart at a time. These guards judge the book:

  - Circuit breaker: stop sending new alerts after a losing streak on a
    symbol, or after the day's total loss passes a limit. Losing streaks
    usually mean the market changed character, and the next signal from
    the same logic is the one most likely to fail too.
  - Correlation guard: do not open what is really the same bet twice.
    EURUSD BUY and GBPUSD BUY are mostly one dollar trade; so are
    EURUSD BUY and USDCHF SELL.

Everything here is pure: callers pass in closed results and price
series, nothing touches the database or the network.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

STREAK_LIMIT = 3            # consecutive SL on one symbol
STREAK_PAUSE_HOURS = 6      # pause that symbol this long after the last loss
DAILY_LOSS_LIMIT_R = 4.0    # all symbols: stop for the day at -4R

CORRELATION_LIMIT = 0.80
CORRELATION_BARS = 120
CORRELATION_MIN_BARS = 40


@dataclass
class ClosedTrade:
    symbol: str
    outcome: str            # TP / SL
    closed_at: datetime
    r: float                # result in risk units: SL = -1, TP = +reward/risk


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def trade_r(outcome: str, entry: float, stop_loss: float, tp: float) -> float:
    """Result of a closed signal in R."""
    if outcome == "SL":
        return -1.0
    risk = abs(float(entry) - float(stop_loss))
    if outcome == "TP" and risk > 0:
        return abs(float(tp) - float(entry)) / risk
    return 0.0


def circuit_breaker_reason(
    symbol: str,
    closed: list[ClosedTrade],
    now: datetime | None = None,
    streak_limit: int = STREAK_LIMIT,
    streak_pause_hours: float = STREAK_PAUSE_HOURS,
    daily_loss_limit_r: float = DAILY_LOSS_LIMIT_R,
) -> str | None:
    """Return why new risk is not allowed right now, or None."""
    now = _utc(now or datetime.now(timezone.utc))
    trades = sorted(closed, key=lambda t: _utc(t.closed_at), reverse=True)

    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_r = sum(t.r for t in trades if _utc(t.closed_at) >= day_start)
    if day_r <= -abs(daily_loss_limit_r):
        return (
            f"Circuit breaker: today is at {day_r:+.1f}R "
            f"(limit -{abs(daily_loss_limit_r):.0f}R). New alerts resume tomorrow UTC"
        )

    streak = 0
    last_loss_at = None
    for t in trades:
        if t.symbol != symbol:
            continue
        if t.outcome != "SL":
            break
        streak += 1
        last_loss_at = last_loss_at or _utc(t.closed_at)

    if streak >= streak_limit and last_loss_at is not None:
        resume_at = last_loss_at + timedelta(hours=streak_pause_hours)
        if now < resume_at:
            minutes = max(1, int((resume_at - now).total_seconds() // 60))
            return (
                f"Circuit breaker: {streak} stop losses in a row on {symbol}. "
                f"Paused for {minutes}m"
            )
    return None


def returns_correlation(
    closes_a: pd.Series,
    closes_b: pd.Series,
    bars: int = CORRELATION_BARS,
) -> float | None:
    """Correlation of bar-to-bar returns over the candles both series share."""
    joined = pd.concat(
        [pd.Series(closes_a).rename("a"), pd.Series(closes_b).rename("b")],
        axis=1, join="inner",
    ).dropna().tail(bars + 1)
    returns = joined.pct_change().dropna()
    if len(returns) < CORRELATION_MIN_BARS:
        return None
    corr = returns["a"].corr(returns["b"])
    return None if pd.isna(corr) else float(corr)


def correlated_exposure_reason(
    symbol: str,
    direction: str,
    open_positions: list[tuple[str, str]],
    closes: dict[str, pd.Series],
    limit: float = CORRELATION_LIMIT,
) -> str | None:
    """Block a signal that duplicates an open trade on a correlated symbol.

    open_positions is [(symbol, direction)] for trades still running.
    Same direction on a positively correlated pair, or opposite direction
    on a negatively correlated pair, is the same bet.
    """
    own = closes.get(symbol)
    if own is None:
        return None

    for other_symbol, other_direction in open_positions:
        if other_symbol == symbol:
            continue
        other = closes.get(other_symbol)
        if other is None:
            continue
        corr = returns_correlation(own, other)
        if corr is None or abs(corr) < limit:
            continue
        same_bet = (corr > 0) == (direction == other_direction)
        if same_bet:
            return (
                f"{direction} blocked: same bet as the open {other_symbol} "
                f"{other_direction} (correlation {corr:+.2f})"
            )
    return None
