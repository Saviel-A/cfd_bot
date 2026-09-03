"""
Backtest harness — replay history through the signal engine and report
what actually happens after each signal.

Runs BOTH engines over the same candles:
  - legacy: the old four-correlated-votes engine with the fixed pip clamp
  - v2:     the orthogonal-factors engine with volatility-scaled stops

For every closed candle it asks the engine for a signal; on BUY/SELL it
enters at the close and walks forward until TP or SL is touched (a candle
that touches both counts as SL — the conservative reading). Results are
reported in R (risk units): TP hit = +rr, SL hit = -1.

This is a historical replay, not a promise: it shares the live scanner's
math but not its ADX/news/session gates, and history never guarantees
the future. Its job is to make engine changes measurable.

Usage:
  .venv/bin/python scripts/backtest.py                  # default symbols
  .venv/bin/python scripts/backtest.py XAUUSD EURUSD    # your picks
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

sys.path.insert(0, ".")

from src.indicators import compute_all  # noqa: E402
from src import signal_engine as v2  # noqa: E402
from src.risk_manager import calculate_trade  # noqa: E402

YF_TICKERS = {
    "XAUUSD": "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "US500": "^GSPC",
    "NAS100": "^NDX",
    "BTCUSD": "BTC-USD",
}

DEFAULT_SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "NAS100", "BTCUSD"]

# Per-symbol replay profiles, mirroring signal_profiles.py: gold trades
# 15m entries against a 1H bias (yfinance caps 15m history at ~60 days);
# everything else runs 1H against 4H.
PROFILES = {
    "XAUUSD": {"interval": "15m", "period": "59d", "htf": "1h"},
}
DEFAULT_PROFILE = {"interval": "1h", "period": "700d", "htf": "4h"}

WARMUP = 210          # candles before the first evaluated signal
MAX_HOLD = 96         # give a trade at most 96 entry-candles to resolve
import os
SETTINGS = {"signals": {"min_confluence": int(os.getenv("MIN_CONF", "4")), "indicators": {}}}
INSTRUMENT_CFG: dict = {}
RISK_V2 = {"sl_atr_multiplier": 1.5, "rr1": float(os.getenv("RR", "2.0")), "sl_min": 5, "sl_max": 16}
RISK_LEGACY = {"sl_atr_multiplier": 1.5, "rr1": 2.0, "sl_min": 12, "sl_max": 16}


def legacy_generate(df, df_htf) -> str:
    """The old engine's decision rule, verbatim in miniature: four
    correlated trend votes, 3-of-4 gate, full-vote counter-trend
    overrides. Kept here so the comparison is honest even after the
    live engine moved on."""
    if df_htf is None or len(df_htf) < 50:
        return "HOLD"
    ema20 = df_htf["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = df_htf["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    bias = "BULLISH" if ema20 > ema50 else "BEARISH" if ema20 < ema50 else "NEUTRAL"

    row = df.iloc[-1]
    rsi = float(row.get("rsi", 50) or 50)
    votes = [
        int(row.get("ema_trend", 0)),
        1 if rsi > 52 else -1 if rsi < 48 else 0,
        int(row.get("macd_trend", 0)),
        int(row.get("bb_signal", 0)),
    ]
    bull = sum(1 for v in votes if v == 1)
    bear = sum(1 for v in votes if v == -1)

    if bias == "BULLISH" and bull >= 3:
        return "BUY"
    if bias == "BEARISH" and bear >= 3:
        return "SELL"
    if bias == "BULLISH" and bear == 4 and rsi >= 35:
        return "SELL"
    if bias == "BEARISH" and bull == 4 and rsi <= 65:
        return "BUY"
    return "HOLD"


@dataclass
class Stats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    unresolved: int = 0
    r_total: float = 0.0

    @property
    def winrate(self) -> float:
        resolved = self.wins + self.losses
        return 100 * self.wins / resolved if resolved else 0.0

    @property
    def expectancy(self) -> float:
        resolved = self.wins + self.losses
        return self.r_total / resolved if resolved else 0.0


def simulate(df: pd.DataFrame, i: int, direction: str, symbol: str, risk_cfg: dict) -> float | None:
    """Enter at candle i's close; walk forward to TP or SL. SL wins ties."""
    entry = float(df["close"].iloc[i])
    atr = float(df["atr"].iloc[i])
    trade = calculate_trade(direction, entry, atr, risk_cfg, symbol=symbol)
    if trade is None:
        return None
    rr = float(risk_cfg.get("rr1", 2.0))

    for j in range(i + 1, min(i + 1 + MAX_HOLD, len(df))):
        high = float(df["high"].iloc[j])
        low = float(df["low"].iloc[j])
        if direction == "BUY":
            if low <= trade.stop_loss:
                return -1.0
            if high >= trade.tp:
                return rr
        else:
            if high >= trade.stop_loss:
                return -1.0
            if low <= trade.tp:
                return rr
    return None  # never resolved inside the window


def run_symbol(symbol: str) -> tuple[Stats, Stats]:
    ticker = YF_TICKERS.get(symbol, symbol)
    profile = PROFILES.get(symbol.upper(), DEFAULT_PROFILE)
    raw = yf.download(
        ticker,
        period=profile["period"],
        interval=profile["interval"],
        progress=False,
        auto_adjust=True,
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"no data for {symbol} ({ticker})")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0].lower() for c in raw.columns]
    else:
        raw.columns = [c.lower() for c in raw.columns]
    df = compute_all(raw, INSTRUMENT_CFG)

    htf_raw = raw.resample(profile["htf"]).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()

    legacy, modern = Stats(), Stats()
    open_until = {"legacy": 0, "v2": 0}

    for i in range(WARMUP, len(df) - 1):
        window = df.iloc[: i + 1]
        htf_window = htf_raw[htf_raw.index <= df.index[i]]

        if i >= open_until["legacy"]:
            direction = legacy_generate(window, htf_window)
            if direction in ("BUY", "SELL"):
                r = simulate(df, i, direction, symbol, RISK_LEGACY)
                legacy.trades += 1
                open_until["legacy"] = i + 6  # one position at a time-ish
                if r is None:
                    legacy.unresolved += 1
                elif r > 0:
                    legacy.wins += 1
                    legacy.r_total += r
                else:
                    legacy.losses += 1
                    legacy.r_total += r

        if i >= open_until["v2"]:
            sig = v2.generate_signal(window, SETTINGS, INSTRUMENT_CFG, df_htf=htf_window)
            if sig.direction in ("BUY", "SELL"):
                r = simulate(df, i, sig.direction, symbol, RISK_V2)
                modern.trades += 1
                open_until["v2"] = i + 6
                if r is None:
                    modern.unresolved += 1
                elif r > 0:
                    modern.wins += 1
                    modern.r_total += r
                else:
                    modern.losses += 1
                    modern.r_total += r

    return legacy, modern


def main() -> None:
    symbols = sys.argv[1:] or DEFAULT_SYMBOLS
    print(f"{'symbol':<8} {'engine':<7} {'trades':>6} {'wins':>5} {'losses':>6} {'win%':>6} {'sumR':>7} {'exp/R':>6}")
    print("-" * 56)
    totals = {"legacy": Stats(), "v2": Stats()}
    for symbol in symbols:
        try:
            legacy, modern = run_symbol(symbol)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"{symbol:<8} skipped: {exc}")
            continue
        for name, stats in (("legacy", legacy), ("v2", modern)):
            total = totals[name]
            total.trades += stats.trades
            total.wins += stats.wins
            total.losses += stats.losses
            total.unresolved += stats.unresolved
            total.r_total += stats.r_total
            print(
                f"{symbol:<8} {name:<7} {stats.trades:>6} {stats.wins:>5} {stats.losses:>6} "
                f"{stats.winrate:>5.1f}% {stats.r_total:>+7.1f} {stats.expectancy:>+6.2f}"
            )
    print("-" * 56)
    for name, stats in totals.items():
        print(
            f"{'TOTAL':<8} {name:<7} {stats.trades:>6} {stats.wins:>5} {stats.losses:>6} "
            f"{stats.winrate:>5.1f}% {stats.r_total:>+7.1f} {stats.expectancy:>+6.2f}"
        )


if __name__ == "__main__":
    main()
