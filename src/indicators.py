"""
Indicators – computes all technical indicators on a OHLCV DataFrame.
Pure pandas/numpy implementation — no pandas-ta dependency needed.
"""

import pandas as pd
import numpy as np


def add_ema(df: pd.DataFrame, fast: int = 9, slow: int = 21) -> pd.DataFrame:
    df = df.copy()
    df[f"ema_{fast}"] = df["close"].ewm(span=fast, adjust=False).mean()
    df[f"ema_{slow}"] = df["close"].ewm(span=slow, adjust=False).mean()

    prev_fast = df[f"ema_{fast}"].shift(1)
    prev_slow = df[f"ema_{slow}"].shift(1)
    df["ema_cross"] = 0
    df.loc[(df[f"ema_{fast}"] > df[f"ema_{slow}"]) & (prev_fast <= prev_slow), "ema_cross"] = 1
    df.loc[(df[f"ema_{fast}"] < df[f"ema_{slow}"]) & (prev_fast >= prev_slow), "ema_cross"] = -1
    df["ema_trend"] = (df[f"ema_{fast}"] > df[f"ema_{slow}"]).map({True: 1, False: -1})
    return df


def add_rsi(df: pd.DataFrame, period: int = 14, oversold: int = 30, overbought: int = 70) -> pd.DataFrame:
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(50)

    # Neutral zone 48-52 votes 0 — avoids false signals at the midline.
    df["rsi_signal"] = 0
    df.loc[df["rsi"] > 52, "rsi_signal"] = 1
    df.loc[df["rsi"] < 48, "rsi_signal"] = -1
    return df


def add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    df = df.copy()
    up_move   = df["high"] - df["high"].shift(1)
    down_move = df["low"].shift(1) - df["low"]

    dm_plus  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    dm_minus = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close  = (df["low"]  - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    atr_s    = tr.ewm(com=period - 1, adjust=False).mean()
    di_plus  = 100 * dm_plus.ewm(com=period  - 1, adjust=False).mean() / atr_s.replace(0, np.nan)
    di_minus = 100 * dm_minus.ewm(com=period - 1, adjust=False).mean() / atr_s.replace(0, np.nan)

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    df["adx"]      = dx.ewm(com=period - 1, adjust=False).mean().fillna(0)
    df["di_plus"]  = di_plus.fillna(0)
    df["di_minus"] = di_minus.fillna(0)
    return df


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    df = df.copy()
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    prev_macd = df["macd"].shift(1)
    prev_signal = df["macd_signal"].shift(1)
    df["macd_cross"] = 0
    df.loc[(df["macd"] > df["macd_signal"]) & (prev_macd <= prev_signal), "macd_cross"] = 1
    df.loc[(df["macd"] < df["macd_signal"]) & (prev_macd >= prev_signal), "macd_cross"] = -1
    df["macd_trend"] = (df["macd"] > df["macd_signal"]).map({True: 1, False: -1})
    return df


def add_bollinger(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    df = df.copy()
    df["bb_mid"] = df["close"].rolling(period).mean()
    rolling_std = df["close"].rolling(period).std()
    df["bb_upper"] = df["bb_mid"] + std_dev * rolling_std
    df["bb_lower"] = df["bb_mid"] - std_dev * rolling_std

    df["bb_signal"] = 0
    df.loc[df["close"] > df["bb_mid"], "bb_signal"] = 1
    df.loc[df["close"] < df["bb_mid"], "bb_signal"] = -1
    df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    df = df.copy()
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = true_range.ewm(com=period - 1, adjust=False).mean()
    return df


def compute_all(df: pd.DataFrame, instrument_cfg: dict) -> pd.DataFrame:
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
    df = add_adx(
        df,
        period=instrument_cfg.get("adx", {}).get("period", 14),
    )
    return df
