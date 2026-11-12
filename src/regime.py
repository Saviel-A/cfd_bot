"""Market regime: is this a market the trend engine should be trading?

Three independent readings, combined into one label:

  - Hurst exponent of recent closes: above 0.5 the series trends
    (moves persist), below 0.5 it mean-reverts (moves fade).
  - Volatility percentile: where the current ATR sits inside its own
    recent history. Very high = shock, very low = dead market.
  - ADX: directional strength, already computed by indicators.py.

The engine is a trend-pullback system, so TREND is its home regime,
RANGE and QUIET are where it bleeds, and SHOCK is where stops sized
from yesterday's ATR are too tight for today's candles.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

HURST_WINDOW = 200
VOL_LOOKBACK = 200

# A pure random walk reads 0.48 +/- 0.05 on this estimator (measured on
# simulated series), so the cut-offs sit outside that noise band.
HURST_TREND = 0.56
HURST_REVERT = 0.40
VOL_SHOCK_PCT = 95.0
VOL_QUIET_PCT = 10.0
ADX_TREND = 20.0


@dataclass
class Regime:
    label: str        # TREND / RANGE / QUIET / SHOCK / MIXED
    hurst: float
    vol_pct: float
    adx: float
    reason: str

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "hurst": self.hurst,
            "vol_pct": self.vol_pct,
            "adx": self.adx,
            "reason": self.reason,
        }


def hurst_exponent(closes: pd.Series, window: int = HURST_WINDOW) -> float:
    """Hurst exponent from how the spread of price changes grows with lag.

    For a random walk the standard deviation of k-step changes grows as
    k^0.5; the fitted exponent is H. Returns 0.5 (no opinion) when there
    is not enough data.
    """
    values = np.log(pd.Series(closes).dropna().astype(float).tail(window).to_numpy())
    if len(values) < 64:
        return 0.5

    # Short lags only: a 200-candle window holds too few independent
    # 16- or 32-step moves, and including them made a random walk read 0.41.
    lags = [1, 2, 4, 8]
    spreads = []
    for lag in lags:
        diff = values[lag:] - values[:-lag]
        spread = float(np.std(diff))
        if spread <= 0:
            return 0.5
        spreads.append(spread)

    slope = np.polyfit(np.log(lags), np.log(spreads), 1)[0]
    return float(min(max(slope, 0.0), 1.0))


def volatility_percentile(atr: pd.Series, lookback: int = VOL_LOOKBACK) -> float:
    """Percent of the last `lookback` ATR readings at or below the current one."""
    history = pd.Series(atr).dropna().tail(lookback)
    if len(history) < 30:
        return 50.0
    current = float(history.iloc[-1])
    return float((history <= current).mean() * 100)


def classify_regime(df: pd.DataFrame) -> Regime:
    """Label the current market from an indicator DataFrame (needs close, atr, adx)."""
    if df is None or df.empty or "close" not in df.columns:
        return Regime("MIXED", 0.5, 50.0, 0.0, "no data")

    hurst = round(hurst_exponent(df["close"]), 2)
    vol_pct = round(volatility_percentile(df["atr"]), 0) if "atr" in df.columns else 50.0
    adx = round(float(df.iloc[-1].get("adx", 0) or 0), 0) if "adx" in df.columns else 0.0

    if vol_pct >= VOL_SHOCK_PCT:
        return Regime("SHOCK", hurst, vol_pct, adx,
                      f"volatility at the {vol_pct:.0f}th percentile of its recent range")
    if vol_pct <= VOL_QUIET_PCT:
        return Regime("QUIET", hurst, vol_pct, adx,
                      f"volatility at the {vol_pct:.0f}th percentile, market is asleep")
    if hurst >= HURST_TREND and adx >= ADX_TREND:
        return Regime("TREND", hurst, vol_pct, adx,
                      f"moves are persisting (Hurst {hurst:.2f}, ADX {adx:.0f})")
    if hurst <= HURST_REVERT:
        return Regime("RANGE", hurst, vol_pct, adx,
                      f"moves are fading back (Hurst {hurst:.2f})")
    return Regime("MIXED", hurst, vol_pct, adx,
                  f"no clear character (Hurst {hurst:.2f}, ADX {adx:.0f})")


def regime_block_reason(regime: Regime) -> str | None:
    """Reason to skip a trend entry in this regime, or None to allow it."""
    if regime.label in ("RANGE", "QUIET", "SHOCK"):
        return f"{regime.label.lower()} regime: {regime.reason}"
    return None
