"""Spot gold candles from Twelve Data.

Yahoo only offers gold as the futures contract (GC=F), which trades at a
different price from the spot gold a CFD follows and arrives delayed.
Twelve Data serves spot XAU/USD without delay on its free plan
(800 requests a day, 8 a minute). It is used only when
TWELVE_DATA_API_KEY is set; without the key the bot stays on Yahoo.
"""

import os

import pandas as pd
import requests

API_URL = "https://api.twelvedata.com/time_series"
MAX_OUTPUT = 5000

# Yahoo ticker -> Twelve Data symbol. Gold only: a full forex watchlist
# would run past the free plan's per-minute limit.
SYMBOLS = {"GC=F": "XAU/USD"}

INTERVALS = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "4h": "1h",   # 4h is resampled from 1h, same as the Yahoo path
    "1d": "1day",
}


def api_key() -> str:
    return os.getenv("TWELVE_DATA_API_KEY", "").strip()


def supports(ticker: str, timeframe: str) -> bool:
    """True when this ticker and timeframe should come from Twelve Data."""
    return bool(api_key()) and ticker in SYMBOLS and timeframe in INTERVALS


def parse_time_series(payload: dict) -> pd.DataFrame:
    """Turn a time_series response into an oldest-first UTC OHLCV frame."""
    if payload.get("status") == "error" or "values" not in payload:
        raise ValueError(f"Twelve Data: {payload.get('message', 'no values returned')}")

    frame = pd.DataFrame(payload["values"])
    if frame.empty:
        raise ValueError("Twelve Data: empty series")

    df = pd.DataFrame(
        {c: pd.to_numeric(frame[c], errors="coerce") for c in ("open", "high", "low", "close")}
    )
    # Spot gold has no exchange volume; the bot treats 0 as "not available".
    df["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0) if "volume" in frame else 0.0
    df.index = pd.to_datetime(frame["datetime"], utc=True)
    df.index.name = None
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df[~df.index.duplicated(keep="last")].sort_index()


def fetch_candles(ticker: str, timeframe: str, bars: int) -> pd.DataFrame:
    """Download up to `bars` candles at the timeframe's native interval."""
    response = requests.get(
        API_URL,
        params={
            "symbol": SYMBOLS[ticker],
            "interval": INTERVALS[timeframe],
            "outputsize": max(1, min(int(bars), MAX_OUTPUT)),
            "timezone": "UTC",
            "apikey": api_key(),
        },
        timeout=10,
    )
    response.raise_for_status()
    return parse_time_series(response.json())
