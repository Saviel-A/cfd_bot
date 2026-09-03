"""
Signal Engine v2 — orthogonal confluence, not four copies of one signal.

The old engine's four votes (EMA trend, MACD>signal, close>BB mid,
RSI>52) all measured the same thing: "price recently rose." They agreed
almost always, so the confluence gate passed whenever price was already
extended — the classic late entry. v2 votes on four genuinely different
questions:

  1. Trend     - is the entry timeframe aligned with the move?
  2. Momentum  - is MACD momentum still BUILDING (histogram slope),
                 not just positive?
  3. RSI zone  - is there room left to run? (BUY 50-68, SELL 32-50;
                 an exhausted RSI votes 0, never with the chase)
  4. Location  - is price near value (within ~1.1 ATR of the slow EMA)
                 rather than stretched at the end of the move?

Plus a hard extension block: beyond 1.6 ATR from value no signal fires
regardless of confluence — that is where pullbacks start, not trades.
Counter-trend overrides are gone entirely; with correlated votes they
were an artifact, and against the HTF trend the odds were worst of all.

Flow:
  1. HTF bias (EMA 20/50) → bullish / bearish / neutral
  2. Four orthogonal factors vote (+1 / -1 / 0)
  3. Must reach min_confluence with the HTF trend, never against it
  4. Extension block overrides everything
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


# Location discipline, in ATRs from the slow EMA.
LOCATION_MAX_ATR = 1.1   # farther than this = no location vote
EXTENSION_BLOCK_ATR = 1.6  # farther than this = no signal at all

# RSI zones with room left to run.
RSI_BUY_MIN, RSI_BUY_MAX = 50.0, 68.0
RSI_SELL_MIN, RSI_SELL_MAX = 32.0, 50.0


@dataclass
class Signal:
    direction: str          # BUY / SELL / HOLD
    strength: int
    total_indicators: int
    details: dict = field(default_factory=dict)
    htf_bias: str = "NEUTRAL"   # BULLISH / BEARISH / NEUTRAL
    candle_time: Optional[str] = None
    current_price: Optional[float] = None
    reason: str = ""
    is_counter_trend: bool = False


def _htf_bias(df_htf: pd.DataFrame) -> str:
    """Determine HTF trend via EMA 20/50."""
    if df_htf is None or len(df_htf) < 50:
        return "NEUTRAL"
    ema20 = df_htf["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = df_htf["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    if ema20 > ema50:
        return "BULLISH"
    elif ema20 < ema50:
        return "BEARISH"
    return "NEUTRAL"


def _vote_trend(row: pd.Series, enabled: bool) -> int:
    return int(row.get("ema_trend", 0)) if enabled else 0


def _vote_momentum(row: pd.Series, enabled: bool) -> int:
    """MACD direction only counts while momentum is still building."""
    if not enabled:
        return 0
    macd_trend = int(row.get("macd_trend", 0))
    hist_rising = int(row.get("macd_hist_rising", 0))
    if macd_trend == 1 and hist_rising == 1:
        return 1
    if macd_trend == -1 and hist_rising == -1:
        return -1
    return 0


def _vote_rsi_zone(row: pd.Series, enabled: bool) -> int:
    """RSI must show room to run, not exhaustion."""
    if not enabled:
        return 0
    rsi = float(row.get("rsi", 50) or 50)
    if RSI_BUY_MIN <= rsi <= RSI_BUY_MAX:
        return 1
    if RSI_SELL_MIN <= rsi <= RSI_SELL_MAX:
        return -1
    return 0


def _vote_location(row: pd.Series, enabled: bool) -> int:
    """Price near value on the right side of it: riding the trend,
    not stretched at the end of it."""
    if not enabled:
        return 0
    dist = float(row.get("ema_dist_atr", 0) or 0)
    if 0.0 <= dist <= LOCATION_MAX_ATR:
        return 1
    if -LOCATION_MAX_ATR <= dist < 0.0:
        return -1
    return 0


def generate_signal(
    df: pd.DataFrame,
    settings: dict,
    instrument_cfg: dict,
    df_htf: pd.DataFrame | None = None,
    htf_label: str = "4H",
    entry_label: str = "1H",
) -> Signal:
    sig_cfg        = settings.get("signals", {})
    min_confluence = sig_cfg.get("min_confluence", 3)
    ind_enabled    = sig_cfg.get("indicators", {})

    latest      = df.iloc[-1]
    candle_time = str(df.index[-1])
    price       = float(latest["close"])
    dist        = float(latest.get("ema_dist_atr", 0) or 0)

    htf_bias = _htf_bias(df_htf)

    votes = {
        "Trend":    _vote_trend(latest,    ind_enabled.get("ema_cross", True)),
        "Momentum": _vote_momentum(latest, ind_enabled.get("macd", True)),
        "RSI zone": _vote_rsi_zone(latest, ind_enabled.get("rsi", True)),
        "Location": _vote_location(latest, ind_enabled.get("bollinger", True)),
    }

    bull_count = sum(1 for v in votes.values() if v == 1)
    bear_count = sum(1 for v in votes.values() if v == -1)

    raw_dir = "HOLD"
    strength = max(bull_count, bear_count)
    reason = ""

    if htf_bias == "NEUTRAL":
        reason = f"{htf_label} trend is neutral"
    elif htf_bias == "BULLISH" and bull_count >= min_confluence:
        if dist > EXTENSION_BLOCK_ATR:
            reason = (
                f"BUY blocked: price is {dist:.1f} ATR above value. "
                "Extended moves get pullbacks, not entries"
            )
        else:
            raw_dir = "BUY"
            strength = bull_count
            reason = f"{htf_label} bullish trend + {entry_label} confluence at value"
    elif htf_bias == "BEARISH" and bear_count >= min_confluence:
        if dist < -EXTENSION_BLOCK_ATR:
            reason = (
                f"SELL blocked: price is {abs(dist):.1f} ATR below value. "
                "Extended moves get pullbacks, not entries"
            )
        else:
            raw_dir = "SELL"
            strength = bear_count
            reason = f"{htf_label} bearish trend + {entry_label} confluence at value"
    else:
        if htf_bias == "BULLISH" and bear_count >= min_confluence:
            reason = f"SELL blocked: counter-trend to {htf_label} bullish bias"
        elif htf_bias == "BEARISH" and bull_count >= min_confluence:
            reason = f"BUY blocked: counter-trend to {htf_label} bearish bias"
        else:
            reason = "Not enough aligned confirmation"

    return Signal(
        direction=raw_dir,
        strength=strength,
        total_indicators=len(votes),
        details=votes,
        htf_bias=htf_bias,
        candle_time=candle_time,
        current_price=price,
        reason=reason,
        is_counter_trend=False,
    )


def signal_summary(signal: Signal, instrument_name: str) -> str:
    icons = {"BUY": "📈 BUY", "SELL": "📉 SELL", "HOLD": "No Signal"}
    return (
        f"{instrument_name} | {icons.get(signal.direction, signal.direction)} | "
        f"HTF: {signal.htf_bias} | "
        f"Strength {signal.strength}/{signal.total_indicators} | "
        f"Price: {signal.current_price:.5f}"
    )
