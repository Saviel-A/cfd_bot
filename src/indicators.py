"""
Indicators – computes all technical indicators on a OHLCV DataFrame.
Uses pandas-ta for calculations.
"""

import pandas as pd
import pandas_ta as ta


def add_ema(df: pd.DataFrame, fast: int = 9, slow: int = 21) -> pd.DataFrame:
    """Add EMA fast/slow and crossover columns."""
    df = df.copy()
    df[f"ema_{fast}"] = ta.ema(df["close"], length=fast)
    df[f"ema_{slow}"] = ta.ema(df["close"], length=slow)

    # crossover: +1 = bullish cross, -1 = bearish cross, 0 = no cross
    prev_fast = df[f"ema_{fast}"].shift(1)
    prev_slow = df[f"ema_{slow}"].shift(1)
    df["ema_cross"] = 0
    df.loc[(df[f"ema_{fast}"] > df[f"ema_{slow}"]) & (prev_fast <= prev_slow), "ema_cross"] = 1
    df.loc[(df[f"ema_{fast}"] < df[f"ema_{slow}"]) & (prev_fast >= prev_slow), "ema_cross"] = -1
    df["ema_trend"] = (df[f"ema_{fast}"] > df[f"ema_{slow}"]).map({True: 1, False: -1})
    return df


def add_rsi(df: pd.DataFrame, period: int = 14, oversold: int = 30, overbought: int = 70) -> pd.DataFrame:
    """Add RSI and signal column."""
    df = df.copy()
    df["rsi"] = ta.rsi(df["close"], length=period)

    # rsi_signal: +1 oversold (buy zone), -1 overbought (sell zone), 0 neutral
    df["rsi_signal"] = 0
    df.loc[df["rsi"] <= oversold, "rsi_signal"] = 1
    df.loc[df["rsi"] >= overbought, "rsi_signal"] = -1
    return df


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Add MACD line, signal line, histogram and crossover signal."""
    df = df.copy()
    macd_df = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
    if macd_df is not None and not macd_df.empty:
        df["macd"] = macd_df.iloc[:, 0]
        df["macd_signal"] = macd_df.iloc[:, 2]
        df["macd_hist"] = macd_df.iloc[:, 1]
    else:
        df["macd"] = 0.0
        df["macd_signal"] = 0.0
        df["macd_hist"] = 0.0

    # macd_cross: +1 bullish (macd crosses above signal), -1 bearish
    prev_macd = df["macd"].shift(1)
    prev_signal = df["macd_signal"].shift(1)
    df["macd_cross"] = 0
    df.loc[(df["macd"] > df["macd_signal"]) & (prev_macd <= prev_signal), "macd_cross"] = 1
    df.loc[(df["macd"] < df["macd_signal"]) & (prev_macd >= prev_signal), "macd_cross"] = -1
    df["macd_trend"] = (df["macd"] > df["macd_signal"]).map({True: 1, False: -1})
    return df


def add_bollinger(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """Add Bollinger Bands and signal."""
    df = df.copy()
    bbands = ta.bbands(df["close"], length=period, std=std_dev)
    if bbands is not None and not bbands.empty:
        df["bb_lower"] = bbands.iloc[:, 0]
        df["bb_mid"] = bbands.iloc[:, 1]
        df["bb_upper"] = bbands.iloc[:, 2]
    else:
        df["bb_lower"] = df["close"]
        df["bb_mid"] = df["close"]
        df["bb_upper"] = df["close"]

    # bb_signal: +1 price at/below lower band (potential buy), -1 at/above upper (potential sell)
    df["bb_signal"] = 0
    df.loc[df["close"] <= df["bb_lower"], "bb_signal"] = 1
    df.loc[df["close"] >= df["bb_upper"], "bb_signal"] = -1

    # Bandwidth squeeze: low bandwidth = breakout incoming
    df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add ATR (Average True Range) for volatility-based SL/TP."""
    df = df.copy()
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=period)
    return df


def compute_all(df: pd.DataFrame, instrument_cfg: dict) -> pd.DataFrame:
    """
    Apply all indicators to the DataFrame using instrument config.

    instrument_cfg keys: rsi, ema, macd, bollinger, atr
    """
    df = add_ema(
        df,
        fast=instrument_cfg.get("ema", {}).get("fast", 9),
        slow=instrument_cfg.get("ema", {}).get("slow", 21),
    )
    df = add_rsi(
        df,
        period=instrument_cfg.get("rsi", {}).get("period", 14),
        oversold=instrument_cfg.get("rsi", {}).get("oversold", 30),
        overbought=instrument_cfg.get("rsi", {}).get("overbought", 70),
    )
    df = add_macd(
        df,
        fast=instrument_cfg.get("macd", {}).get("fast", 12),
        slow=instrument_cfg.get("macd", {}).get("slow", 26),
        signal=instrument_cfg.get("macd", {}).get("signal", 9),
    )
    df = add_bollinger(
        df,
        period=instrument_cfg.get("bollinger", {}).get("period", 20),
        std_dev=instrument_cfg.get("bollinger", {}).get("std_dev", 2.0),
    )
    df = add_atr(
        df,
        period=instrument_cfg.get("atr", {}).get("period", 14),
    )
    return df
