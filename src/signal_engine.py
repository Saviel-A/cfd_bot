"""
Signal Engine – aggregates indicator readings into a final BUY / SELL / HOLD decision.

Logic:
  - Each enabled indicator casts a vote: +1 (bullish), -1 (bearish), 0 (neutral)
  - If total bullish votes >= min_confluence  → BUY
  - If total bearish votes >= min_confluence  → SELL
  - Otherwise                                 → HOLD

This multi-indicator confluence approach reduces false signals significantly.
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class Signal:
    direction: str          # "BUY", "SELL", "HOLD"
    strength: int           # number of agreeing indicators (0–5)
    total_indicators: int   # how many indicators were active
    details: dict = field(default_factory=dict)   # per-indicator votes
    candle_time: Optional[str] = None
    current_price: Optional[float] = None


def _vote_ema(row: pd.Series, enabled: bool) -> int:
    if not enabled:
        return 0
    # Use trend direction (not just crossover) for stronger signal
    return int(row.get("ema_trend", 0))


def _vote_rsi(row: pd.Series, enabled: bool) -> int:
    if not enabled:
        return 0
    return int(row.get("rsi_signal", 0))


def _vote_macd(row: pd.Series, enabled: bool) -> int:
    if not enabled:
        return 0
    return int(row.get("macd_trend", 0))


def _vote_bollinger(row: pd.Series, enabled: bool) -> int:
    if not enabled:
        return 0
    return int(row.get("bb_signal", 0))


def _vote_atr(row: pd.Series, enabled: bool) -> int:
    """ATR doesn't cast direction votes – it's used for SL/TP sizing only."""
    return 0


def generate_signal(df: pd.DataFrame, settings: dict, instrument_cfg: dict) -> Signal:
    """
    Analyse the latest candle and return a Signal.

    df             – DataFrame with all indicators applied
    settings       – global settings dict (signals section)
    instrument_cfg – per-instrument YAML config
    """
    sig_cfg = settings.get("signals", {})
    min_confluence = sig_cfg.get("min_confluence", 3)
    ind_enabled = sig_cfg.get("indicators", {})

    latest = df.iloc[-1]
    candle_time = str(df.index[-1])
    price = float(latest["close"])

    votes = {
        "EMA trend":     _vote_ema(latest,       ind_enabled.get("ema_cross",  True)),
        "RSI":           _vote_rsi(latest,       ind_enabled.get("rsi",         True)),
        "MACD":          _vote_macd(latest,      ind_enabled.get("macd",        True)),
        "Bollinger":     _vote_bollinger(latest, ind_enabled.get("bollinger",   True)),
    }

    bull_count = sum(1 for v in votes.values() if v == 1)
    bear_count = sum(1 for v in votes.values() if v == -1)
    active = sum(1 for v in votes.values() if v != 0)

    if bull_count >= min_confluence:
        direction = "BUY"
        strength = bull_count
    elif bear_count >= min_confluence:
        direction = "SELL"
        strength = bear_count
    else:
        direction = "HOLD"
        strength = max(bull_count, bear_count)

    return Signal(
        direction=direction,
        strength=strength,
        total_indicators=len(votes),
        details=votes,
        candle_time=candle_time,
        current_price=price,
    )


def signal_summary(signal: Signal, instrument_name: str) -> str:
    """Return a formatted one-line summary of the signal."""
    icons = {"BUY": "▲ BUY", "SELL": "▼ SELL", "HOLD": "◆ HOLD"}
    icon = icons.get(signal.direction, signal.direction)
    return (
        f"{instrument_name} | {icon} | "
        f"Strength {signal.strength}/{signal.total_indicators} | "
        f"Price: {signal.current_price:.5f}"
    )
