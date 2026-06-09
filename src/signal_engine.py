"""
Signal Engine — multi-timeframe confluence gate.

Flow:
  1. 4H bias (EMA 50/200) → bullish / bearish / neutral
  2. 1H indicators vote (+1 / -1 / 0)
  3. Counter-trend signals blocked unless all 1H indicators override
  4. Must reach min_confluence threshold
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


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
    """Determine 4H trend via EMA 50/200 (institutional golden/death cross)."""
    if df_htf is None or len(df_htf) < 200:
        return "NEUTRAL"
    ema50  = df_htf["close"].ewm(span=50,  adjust=False).mean().iloc[-1]
    ema200 = df_htf["close"].ewm(span=200, adjust=False).mean().iloc[-1]
    if ema50 > ema200:
        return "BULLISH"
    elif ema50 < ema200:
        return "BEARISH"
    return "NEUTRAL"


def _vote_ema(row: pd.Series, enabled: bool) -> int:
    return int(row.get("ema_trend", 0)) if enabled else 0


def _vote_rsi(row: pd.Series, enabled: bool) -> int:
    return int(row.get("rsi_signal", 0)) if enabled else 0


def _vote_macd(row: pd.Series, enabled: bool) -> int:
    return int(row.get("macd_trend", 0)) if enabled else 0


def _vote_bollinger(row: pd.Series, enabled: bool) -> int:
    return int(row.get("bb_signal", 0)) if enabled else 0


def generate_signal(
    df: pd.DataFrame,
    settings: dict,
    instrument_cfg: dict,
    df_htf: pd.DataFrame | None = None,
    htf_label: str = "4H",
    entry_label: str = "1H",
) -> Signal:
    sig_cfg       = settings.get("signals", {})
    min_confluence = sig_cfg.get("min_confluence", 3)
    ind_enabled   = sig_cfg.get("indicators", {})

    latest      = df.iloc[-1]
    candle_time = str(df.index[-1])
    price       = float(latest["close"])
    rsi         = float(latest.get("rsi", 50) or 50)

    htf_bias = _htf_bias(df_htf)

    votes = {
        "EMA trend": _vote_ema(latest,      ind_enabled.get("ema_cross",  True)),
        "RSI":       _vote_rsi(latest,      ind_enabled.get("rsi",         True)),
        "MACD":      _vote_macd(latest,     ind_enabled.get("macd",        True)),
        "Bollinger": _vote_bollinger(latest, ind_enabled.get("bollinger",   True)),
    }

    bull_count = sum(1 for v in votes.values() if v == 1)
    bear_count = sum(1 for v in votes.values() if v == -1)

    reason = ""
    is_counter_trend = False

    if htf_bias == "NEUTRAL":
        raw_dir = "HOLD"
        strength = max(bull_count, bear_count)
        reason = f"{htf_label} trend is neutral"
    elif htf_bias == "BULLISH" and bull_count >= min_confluence:
        raw_dir = "BUY"
        strength = bull_count
        reason = f"{htf_label} bullish trend + {entry_label} bullish confirmation"
    elif htf_bias == "BEARISH" and bear_count >= min_confluence:
        raw_dir = "SELL"
        strength = bear_count
        reason = f"{htf_label} bearish trend + {entry_label} bearish confirmation"
    elif htf_bias == "BULLISH" and bear_count == len(votes):
        raw_dir = "SELL"
        strength = bear_count
        reason = f"{htf_label} bullish + full {entry_label} bearish override"
        is_counter_trend = True
        if rsi < 35:
            raw_dir = "HOLD"
            reason = f"SELL blocked: RSI is oversold + counter-trend to {htf_label} bullish bias"
            is_counter_trend = False
    elif htf_bias == "BEARISH" and bull_count == len(votes):
        raw_dir = "BUY"
        strength = bull_count
        reason = f"{htf_label} bearish + full {entry_label} bullish override"
        is_counter_trend = True
        if rsi > 65:
            raw_dir = "HOLD"
            reason = f"BUY blocked: RSI is overbought + counter-trend to {htf_label} bearish bias"
            is_counter_trend = False
    else:
        raw_dir = "HOLD"
        strength = max(bull_count, bear_count)
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
        is_counter_trend=is_counter_trend,
    )


def signal_summary(signal: Signal, instrument_name: str) -> str:
    icons = {"BUY": "📈 BUY", "SELL": "📉 SELL", "HOLD": "No Signal"}
    return (
        f"{instrument_name} | {icons.get(signal.direction, signal.direction)} | "
        f"HTF: {signal.htf_bias} | "
        f"Strength {signal.strength}/{signal.total_indicators} | "
        f"Price: {signal.current_price:.5f}"
    )
