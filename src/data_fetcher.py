"""
Data Fetcher – downloads OHLCV candle data from Yahoo Finance.
Supports any yfinance ticker symbol.
"""

import ssl
import json
import urllib.request
from datetime import datetime, timezone, timedelta

import yfinance as yf
import pandas as pd

_SSL_CTX = ssl._create_unverified_context()

# Swissquote live spot feed (forex + metals)
_SQ_URL = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/{base}/{quote}"

_SWISSQUOTE_MAP: dict[str, tuple[str, str]] = {
    # Metals
    "XAUUSD": ("XAU", "USD"), "GOLD":   ("XAU", "USD"),
    "XAGUSD": ("XAG", "USD"), "SILVER": ("XAG", "USD"),
    "XPTUSD": ("XPT", "USD"),
    # Major forex
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"), "USDCHF": ("USD", "CHF"),
    "AUDUSD": ("AUD", "USD"), "NZDUSD": ("NZD", "USD"),
    "USDCAD": ("USD", "CAD"),
    # Minor forex
    "EURGBP": ("EUR", "GBP"), "EURJPY": ("EUR", "JPY"),
    "GBPJPY": ("GBP", "JPY"), "EURCHF": ("EUR", "CHF"),
    "GBPCHF": ("GBP", "CHF"), "AUDJPY": ("AUD", "JPY"),
    "CADJPY": ("CAD", "JPY"), "CHFJPY": ("CHF", "JPY"),
    "AUDCAD": ("AUD", "CAD"), "AUDCHF": ("AUD", "CHF"),
    "CADCHF": ("CAD", "CHF"), "NZDJPY": ("NZD", "JPY"),
    "EURAUD": ("EUR", "AUD"), "EURCAD": ("EUR", "CAD"),
    "EURNZD": ("EUR", "NZD"), "GBPAUD": ("GBP", "AUD"),
    "GBPCAD": ("GBP", "CAD"), "GBPNZD": ("GBP", "NZD"),
}


def _swissquote_quote(symbol: str) -> dict[str, float] | None:
    pair = _SWISSQUOTE_MAP.get(symbol.upper())
    if not pair:
        return None
    url = _SQ_URL.format(base=pair[0], quote=pair[1])
    for _ in range(3):  # retry up to 3x on transient failures
        try:
            with urllib.request.urlopen(url, timeout=5, context=_SSL_CTX) as resp:
                data = json.loads(resp.read())
            if data:
                prices = data[0]["spreadProfilePrices"]
                bid = float(prices[0]["bid"])
                ask = float(prices[0]["ask"])
                return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}
        except Exception:
            pass
    return None


def _swissquote_price(symbol: str) -> float | None:
    quote = _swissquote_quote(symbol)
    return quote["mid"] if quote else None


# CoinGecko live prices (crypto)
_CG_URL = "https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"

_COINGECKO_MAP: dict[str, str] = {
    "BTC": "bitcoin",     "BTCUSD": "bitcoin",
    "ETH": "ethereum",    "ETHUSD": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "LINK": "chainlink",
    "LTC": "litecoin",
}


def _coingecko_price(symbol: str) -> float | None:
    cg_id = _COINGECKO_MAP.get(symbol.upper())
    if not cg_id:
        return None
    try:
        url = _CG_URL.format(ids=cg_id)
        with urllib.request.urlopen(url, timeout=5, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        return float(data[cg_id]["usd"])
    except Exception:
        pass
    return None


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


def _timeframe_delta(timeframe: str) -> timedelta | None:
    if timeframe.endswith("m"):
        return timedelta(minutes=int(timeframe[:-1]))
    if timeframe.endswith("h"):
        return timedelta(hours=int(timeframe[:-1]))
    if timeframe.endswith("d"):
        return timedelta(days=int(timeframe[:-1]))
    return None


def _drop_incomplete_candle(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Remove the currently forming candle so signals use closed candles only."""
    if df.empty:
        return df

    delta = _timeframe_delta(timeframe)
    if delta is None:
        return df

    last_start = pd.Timestamp(df.index[-1])
    if last_start.tzinfo is None:
        last_start = last_start.tz_localize(timezone.utc)
    else:
        last_start = last_start.tz_convert(timezone.utc)

    if last_start + delta > datetime.now(timezone.utc):
        return df.iloc[:-1].copy()
    return df


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

    df = _drop_incomplete_candle(df, timeframe)

    # Keep only the last N candles
    df = df.tail(lookback).copy()
    df.index = pd.to_datetime(df.index)

    return df


def get_current_price(ticker: str) -> float:
    """Return the latest price for a ticker using yf.download (works for all ticker types)."""
    def _last_close(t: str, interval: str, period: str) -> float | None:
        try:
            raw = yf.download(t, interval=interval, period=period, auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                return None
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.droplevel(1)
            val = float(raw["Close"].iloc[-1])
            return val if val > 0 else None
        except Exception:
            return None

    price = (_last_close(ticker, "1m", "1d") or
             _last_close(ticker, "1h", "5d") or
             _last_close(ticker, "1h", "60d"))
    if price:
        return price
    raise ConnectionError(f"Could not get price for {ticker}")


def get_live_price(symbol: str) -> float:
    """
    Return the best live price for a symbol.
    Priority:
      1. Swissquote — forex + metals (spot, matches TradingView/CFD brokers)
      2. CoinGecko  — crypto (real-time, free, no key)
      3. yfinance   — indices, stocks, energy (fallback)
    """
    from src.instruments import get_ticker_for_symbol

    price = _swissquote_price(symbol) or _coingecko_price(symbol)
    if price:
        return price

    ticker = get_ticker_for_symbol(symbol)
    return get_current_price(ticker)


def get_live_quote(symbol: str) -> dict[str, float]:
    """
    Return bid/ask/mid when available.
    For feeds that only provide one price, bid/ask/mid all use that value.
    """
    quote = _swissquote_quote(symbol)
    if quote:
        return quote

    price = _coingecko_price(symbol)
    if price is None:
        from src.instruments import get_ticker_for_symbol

        price = get_current_price(get_ticker_for_symbol(symbol))
    return {"bid": price, "ask": price, "mid": price}
