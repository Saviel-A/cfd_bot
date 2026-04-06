"""
Signal Engine — multi-timeframe confluence gate.

Flow:
  1. 4H bias (EMA 50/200) → bullish / bearish / neutral
  2. 1H indicators vote (+1 / -1 / 0)
  3. Counter-trend signals discarded
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


def _htf_bias(df_htf: pd.DataFrame) -> str:
    """Determine 4H trend via EMA 50/200."""
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
) -> Signal:
    sig_cfg       = settings.get("signals", {})
    min_confluence = sig_cfg.get("min_confluence", 3)
    ind_enabled   = sig_cfg.get("indicators", {})

    latest      = df.iloc[-1]
    candle_time = str(df.index[-1])
    price       = float(latest["close"])

    htf_bias = _htf_bias(df_htf)

    votes = {
        "EMA trend": _vote_ema(latest,      ind_enabled.get("ema_cross",  True)),
        "RSI":       _vote_rsi(latest,      ind_enabled.get("rsi",         True)),
        "MACD":      _vote_macd(latest,     ind_enabled.get("macd",        True)),
        "Bollinger": _vote_bollinger(latest, ind_enabled.get("bollinger",   True)),
    }

    bull_count = sum(1 for v in votes.values() if v == 1)
    bear_count = sum(1 for v in votes.values() if v == -1)

    # Determine raw direction
    if bull_count >= min_confluence:
        raw_dir = "BUY"
        strength = bull_count
    elif bear_count >= min_confluence:
        raw_dir = "SELL"
        strength = bear_count
    else:
        raw_dir = "HOLD"
        strength = max(bull_count, bear_count)

    # HTF bias gate — discard counter-trend signals
    if raw_dir == "BUY"  and htf_bias == "BEARISH":
        raw_dir = "HOLD"
    if raw_dir == "SELL" and htf_bias == "BULLISH":
        raw_dir = "HOLD"

    return Signal(
        direction=raw_dir,
        strength=strength,
        total_indicators=len(votes),
        details=votes,
        htf_bias=htf_bias,
        candle_time=candle_time,
        current_price=price,
    )


def signal_summary(signal: Signal, instrument_name: str) -> str:
    icons = {"BUY": "▲ BUY", "SELL": "▼ SELL", "HOLD": "◆ HOLD"}
    return (
        f"{instrument_name} | {icons.get(signal.direction, signal.direction)} | "
        f"HTF: {signal.htf_bias} | "
        f"Strength {signal.strength}/{signal.total_indicators} | "
        f"Price: {signal.current_price:.5f}"
    )
