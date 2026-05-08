"""Chart generator — dark TradingView-style candlestick chart with EMA + RSI."""

import io
import logging
import matplotlib
matplotlib.use("Agg")  # must be set before any other matplotlib import

import mplfinance as mpf
import pandas as pd

import numpy as np

from src.data_fetcher import fetch_ohlcv, get_live_price
from src.indicators import compute_all
from src.instruments import load_instrument_cfg, get_display_name

logger = logging.getLogger(__name__)

_MARKET_COLORS = mpf.make_marketcolors(
    up="#26a69a",   down="#ef5350",
    edge={"up": "#26a69a", "down": "#ef5350"},
    wick={"up": "#aaaaaa", "down": "#aaaaaa"},
    volume={"up": "#26a69a80", "down": "#ef535080"},
)

_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=_MARKET_COLORS,
    facecolor="#131722",
    edgecolor="#2a2e39",
    figcolor="#131722",
    gridcolor="#2a2e39",
    gridstyle="-",
    gridaxis="both",
    y_on_right=True,
    rc={
        "font.size": 10,
        "axes.labelcolor": "#d1d4dc",
        "xtick.color": "#787b86",
        "ytick.color": "#787b86",
    },
)


def generate_chart(symbol: str, candles: int = 80, live_price: float | None = None) -> io.BytesIO:
    cfg_inst = load_instrument_cfg(symbol)
    ticker   = cfg_inst.get("ticker", symbol)
    name     = get_display_name(symbol)

    df = fetch_ohlcv(ticker, timeframe="1h", lookback=candles + 60)
    df = compute_all(df, cfg_inst)
    df = df.tail(candles).copy()

    if df.empty:
        raise ValueError(f"No chart data for {symbol}")

    # Use passed-in live price (so chart matches alert), fetch only if not provided
    if live_price is None:
        try:
            live_price = get_live_price(symbol)
        except Exception:
            live_price = float(df["close"].iloc[-1])

    plot_df = df[["open", "high", "low", "close", "volume"]].copy()
    plot_df.columns = ["Open", "High", "Low", "Close", "Volume"]
    plot_df.index = pd.to_datetime(plot_df.index)

    fast_col = f"ema_{cfg_inst.get('ema', {}).get('fast', 9)}"
    slow_col = f"ema_{cfg_inst.get('ema', {}).get('slow', 21)}"

    n = len(df)
    adds = []
    if fast_col in df.columns:
        adds.append(mpf.make_addplot(df[fast_col].values, color="#f39c12", width=1.0, panel=0))
    if slow_col in df.columns:
        adds.append(mpf.make_addplot(df[slow_col].values, color="#3498db", width=1.0, panel=0))

    # Live price horizontal line
    adds.append(mpf.make_addplot(
        np.full(n, live_price), color="#ffffff", width=0.8,
        linestyle="--", panel=0, secondary_y=False,
    ))

    if "rsi" in df.columns:
        adds.append(mpf.make_addplot(df["rsi"].values,   panel=2, color="#9b59b6", width=1.2, ylabel="RSI", secondary_y=False))
        adds.append(mpf.make_addplot([70] * n,           panel=2, color="#ef5350", width=0.7, linestyle="--", secondary_y=False))
        adds.append(mpf.make_addplot([30] * n,           panel=2, color="#26a69a", width=0.7, linestyle="--", secondary_y=False))

    # Format price for title
    decimals = 5 if live_price < 10 else (2 if live_price < 1000 else 2)
    price_str = f"{live_price:,.{decimals}f}"

    buf = io.BytesIO()
    mpf.plot(
        plot_df,
        type="candle",
        style=_STYLE,
        title=f"\n{name}  ·  1H  ·  {price_str}",
        volume=True,
        addplot=adds,
        panel_ratios=(4, 1, 1.5),
        figsize=(14, 8),
        tight_layout=True,
        savefig=dict(fname=buf, dpi=130, bbox_inches="tight", facecolor="#131722"),
    )
    buf.seek(0)
    return buf
