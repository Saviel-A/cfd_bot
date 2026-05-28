"""Gold-specific intraday signal adjustments."""

from src.market_pressure import MarketPressure

GOLD_SYMBOLS = {"XAUUSD", "GOLD"}


def _vote_counts(signal) -> tuple[int, int]:
    votes = signal.details or {}
    bull = sum(1 for value in votes.values() if value == 1)
    bear = sum(1 for value in votes.values() if value == -1)
    return bull, bear


def _protected_hold_reason(reason: str) -> bool:
    text = (reason or "").lower()
    return (
        "candle feed" in text
        or "risk levels" in text
        or "rsi is overbought" in text
        or "rsi is oversold" in text
    )


def apply_gold_momentum(symbol: str, signal, pressure: MarketPressure) -> None:
    """Promote high-pressure Gold continuation setups.

    This catches continuation alerts without trading pressure alone. It still
    requires the 1H direction, strong pressure, and at least 3 of 4 15M votes.
    """
    if symbol.upper() not in GOLD_SYMBOLS or signal.direction != "HOLD":
        return
    if _protected_hold_reason(signal.reason):
        return

    bull_count, bear_count = _vote_counts(signal)

    if (
        signal.htf_bias == "BEARISH"
        and bear_count >= 3
        and pressure.direction == "SELLERS"
        and pressure.sell_pct >= 65
    ):
        signal.direction = "SELL"
        signal.strength = bear_count
        signal.reason = "1H bearish + seller momentum"
        signal.is_counter_trend = False
        return

    if (
        signal.htf_bias == "BULLISH"
        and bull_count >= 3
        and pressure.direction == "BUYERS"
        and pressure.buy_pct >= 65
    ):
        signal.direction = "BUY"
        signal.strength = bull_count
        signal.reason = "1H bullish + buyer momentum"
        signal.is_counter_trend = False
