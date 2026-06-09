"""Recent candle pressure filter.

This is not broker order-flow or a live long/short ratio. It estimates whether
recent closed candles show more buying or selling pressure before an alert fires.
"""

from dataclasses import dataclass

import pandas as pd

GOLD_SYMBOLS = {"XAUUSD", "GOLD"}
GOLD_MIN_PRESSURE = 60
GOLD_OPPOSITE_PRESSURE_BLOCK = 55


@dataclass
class MarketPressure:
    direction: str
    buy_pct: float
    sell_pct: float
    score: float
    reason: str

    @property
    def aligned_direction(self) -> str:
        if self.direction == "BUYERS":
            return "BUY"
        if self.direction == "SELLERS":
            return "SELL"
        return "MIXED"

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "buy_pct": self.buy_pct,
            "sell_pct": self.sell_pct,
            "score": self.score,
            "reason": self.reason,
        }


def analyze_market_pressure(df: pd.DataFrame, lookback: int = 20) -> MarketPressure:
    """Estimate buyer/seller pressure from recent closed candles."""
    if df is None or df.empty:
        return MarketPressure("MIXED", 50.0, 50.0, 0.0, "no candle data")

    recent = df.tail(lookback).copy()
    if len(recent) < 6:
        return MarketPressure("MIXED", 50.0, 50.0, 0.0, "not enough candles")

    candle_range = (recent["high"] - recent["low"]).abs()
    candle_range = candle_range.where(candle_range != 0)
    body_ratio = ((recent["close"] - recent["open"]) / candle_range).astype("float64").fillna(0).clip(-1, 1)

    if "volume" in recent.columns and float(recent["volume"].fillna(0).sum()) > 0:
        weights = recent["volume"].fillna(0).astype(float)
    else:
        weights = pd.Series(1.0, index=recent.index)

    buy_pressure = float((body_ratio.clip(lower=0) * weights).sum())
    sell_pressure = float((-body_ratio.clip(upper=0) * weights).sum())
    total = buy_pressure + sell_pressure

    if total <= 0:
        return MarketPressure("MIXED", 50.0, 50.0, 0.0, "flat recent candles")

    buy_pct = buy_pressure / total * 100
    sell_pct = sell_pressure / total * 100
    score = abs(buy_pct - sell_pct)

    last3 = body_ratio.tail(3).sum()
    if buy_pct >= 55 and last3 > 0:
        direction = "BUYERS"
        reason = f"buyers control recent candles ({buy_pct:.0f}% vs {sell_pct:.0f}%)"
    elif sell_pct >= 55 and last3 < 0:
        direction = "SELLERS"
        reason = f"sellers control recent candles ({sell_pct:.0f}% vs {buy_pct:.0f}%)"
    else:
        direction = "MIXED"
        reason = f"mixed pressure ({buy_pct:.0f}% buy vs {sell_pct:.0f}% sell)"

    return MarketPressure(direction, round(buy_pct, 1), round(sell_pct, 1), round(score, 1), reason)


def pressure_confirms(direction: str, pressure: MarketPressure) -> bool:
    """True when recent pressure supports the proposed signal direction."""
    return pressure.aligned_direction == direction


def should_block_by_pressure(symbol: str, signal, pressure: MarketPressure) -> bool:
    """Return True when pressure should block a signal.

    Gold is intentionally more active, but strong opposite pressure still blocks:
    - Gold must stay aligned with the higher-timeframe direction
    - trend-aligned Gold setups still need pressure confirmation
    """
    if symbol.upper() in GOLD_SYMBOLS and signal.is_counter_trend:
        return True

    if symbol.upper() in GOLD_SYMBOLS:
        if signal.direction == "BUY":
            return (
                pressure.buy_pct < GOLD_MIN_PRESSURE
                or pressure.sell_pct >= GOLD_OPPOSITE_PRESSURE_BLOCK
            )
        if signal.direction == "SELL":
            return (
                pressure.sell_pct < GOLD_MIN_PRESSURE
                or pressure.buy_pct >= GOLD_OPPOSITE_PRESSURE_BLOCK
            )

    if pressure_confirms(signal.direction, pressure):
        return False

    strong_opposite = (
        (signal.direction == "BUY" and pressure.direction == "SELLERS" and pressure.sell_pct >= 65)
        or (signal.direction == "SELL" and pressure.direction == "BUYERS" and pressure.buy_pct >= 65)
    )
    if strong_opposite:
        return True

    return True
