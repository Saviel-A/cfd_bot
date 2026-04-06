"""
Data Fetcher – downloads OHLCV candle data from Yahoo Finance.
Supports any yfinance ticker symbol.
"""

import yfinance as yf
import pandas as pd
from datetime import datetime


# Map human-friendly timeframe strings to yfinance interval + period params
TIMEFRAME_MAP = {
    "1m":  {"interval": "1m",  "period": "7d"},
    "5m":  {"interval": "5m",  "period": "60d"},
    "15m": {"interval": "15m", "period": "60d"},
    "30m": {"interval": "30m", "period": "60d"},
    "1h":  {"interval": "1h",  "period": "730d"},
    "4h":  {"interval": "1h",  "period": "730d"},   # resample from 1h
    "1d":  {"interval": "1d",  "period": "5y"},
}


def fetch_ohlcv(ticker: str, timeframe: str = "1h", lookback: int = 200) -> pd.DataFrame:
    """
    Fetch OHLCV data for a ticker.

    Returns a DataFrame with columns: open, high, low, close, volume
    Index is DatetimeIndex (UTC).
    """
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Choose from: {list(TIMEFRAME_MAP)}")

    params = TIMEFRAME_MAP[timeframe]

    try:
        raw = yf.download(
            ticker,
            interval=params["interval"],
            period=params["period"],
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        raise ConnectionError(f"Failed to fetch data for {ticker}: {e}")

    if raw is None or raw.empty:
        raise ValueError(f"No data returned for ticker '{ticker}'. Check the symbol.")

    # Flatten multi-level columns if present
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.droplevel(1)

    df = raw.copy()
    df.columns = [c.lower() for c in df.columns]

    # Resample to 4h if requested
    if timeframe == "4h":
        df = df.resample("4h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()

    # Keep only the last N candles
    df = df.tail(lookback).copy()
    df.index = pd.to_datetime(df.index)

    return df


def get_current_price(ticker: str) -> float:
    """Return the latest closing price for a ticker."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1d", interval="1m")
        if hist.empty:
            hist = t.history(period="5d", interval="1h")
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        raise ConnectionError(f"Could not get price for {ticker}: {e}")
