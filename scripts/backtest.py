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

Honesty checks built in:
  - Costs: every v2 trade pays COST_R (spread + slippage, in R). A
    strategy that only works at zero cost does not work.
  - Walk-forward: v2 trades are split into FOLDS consecutive periods
    and each is reported separately. An edge that lives in one period
    and dies in the rest is a curve fit, not an edge.

  - Robustness: the same entries are replayed with the stop and the
    target moved 25% either way and with double costs. A real edge is
    a plateau; one that needs exact settings is a curve fit.
  - Verdict: a short pass/fail checklist (enough trades, positive
    after costs, holds across time, survives the stress variants).

Usage:
  .venv/bin/python scripts/backtest.py                  # default symbols
  .venv/bin/python scripts/backtest.py XAUUSD EURUSD    # your picks

Price history comes from Yahoo by default. To test on your broker's own
prices (the real CFD feed), export bars from MetaTrader 5
(View > Symbols > Bars > Export Bars) at the entry timeframe or finer
(M15 for gold, H1 for the rest), save them as <folder>/XAUUSD.csv,
<folder>/EURUSD.csv ... and run:
  DATA_DIR=<folder> .venv/bin/python scripts/backtest.py XAUUSD EURUSD

Experiments (environment variables, v2 engine only):
  REGIME_GATE=1   skip entries in range / quiet / shock regimes
  BE_AT_R=1.0     move the stop to entry once price is 1R in profit
  COST_R=0.05     cost per trade in R (default 0.05)
  FOLDS=4         walk-forward periods (default 4)
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
from src.regime import classify_regime, regime_block_reason  # noqa: E402
from src.history import find_history_file, load_history_file, resample_ohlcv  # noqa: E402

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
COST_R = float(os.getenv("COST_R", "0.05"))
REGIME_GATE = os.getenv("REGIME_GATE", "0") == "1"
BE_AT_R = float(os.getenv("BE_AT_R", "0"))   # 0 = breakeven stop off
FOLDS = max(1, int(os.getenv("FOLDS", "4")))
DATA_DIR = os.getenv("DATA_DIR", "")

# Robustness variants: (label, stop multiplier, target multiplier, cost multiplier)
STRESS = [
    ("baseline", 1.0, 1.0, 1.0),
    ("stop 25% tighter", 0.75, 1.0, 1.0),
    ("stop 25% wider", 1.25, 1.0, 1.0),
    ("target 20% closer", 1.0, 0.8, 1.0),
    ("target 20% farther", 1.0, 1.2, 1.0),
    ("double costs", 1.0, 1.0, 2.0),
]
MIN_TRADES = 30
GOOD_TRADES = 100
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
    scratches: int = 0     # stopped at breakeven

    def record(self, r: float | None, cost: float = 0.0) -> None:
        """Count a result by how the trade ended; the cost only reduces R."""
        self.trades += 1
        if r is None:
            self.unresolved += 1
            return
        self.r_total += r - cost
        if r > 0:
            self.wins += 1
        elif r < 0:
            self.losses += 1
        else:
            self.scratches += 1

    @property
    def winrate(self) -> float:
        resolved = self.wins + self.losses
        return 100 * self.wins / resolved if resolved else 0.0

    @property
    def expectancy(self) -> float:
        resolved = self.wins + self.losses + self.scratches
        return self.r_total / resolved if resolved else 0.0


def fold_stats(trades: list[tuple[pd.Timestamp, float]], folds: int) -> list[Stats]:
    """Split resolved trades into consecutive periods, oldest first."""
    ordered = sorted(trades, key=lambda t: t[0])
    if not ordered:
        return []
    folds = min(folds, len(ordered))
    size = len(ordered) / folds
    out = []
    for k in range(folds):
        stats = Stats()
        for _, r in ordered[round(k * size): round((k + 1) * size)]:
            stats.record(r, cost=COST_R)
        out.append(stats)
    return out


def simulate(
    df: pd.DataFrame, i: int, direction: str, symbol: str, risk_cfg: dict,
    be_at_r: float = 0.0, stop_mult: float = 1.0, target_mult: float = 1.0,
) -> float | None:
    """Enter at candle i's close; walk forward to TP or SL. SL wins ties.

    With be_at_r > 0 the stop moves to entry once a candle has closed
    that far in profit; a later return to entry is a 0R scratch.
    stop_mult / target_mult move the stop and the target away from
    where the risk manager put them, for robustness checks. The result
    is in R of the stop actually used.
    """
    entry = float(df["close"].iloc[i])
    atr = float(df["atr"].iloc[i])
    trade = calculate_trade(direction, entry, atr, risk_cfg, symbol=symbol)
    if trade is None:
        return None
    side = 1 if direction == "BUY" else -1
    sl_dist = trade.sl_distance * stop_mult
    tp_dist = abs(trade.tp - trade.entry_price) * target_mult
    if sl_dist <= 0 or tp_dist <= 0:
        return None
    rr = tp_dist / sl_dist
    stop = entry - side * sl_dist
    target = entry + side * tp_dist

    stop_r = -1.0
    for j in range(i + 1, min(i + 1 + MAX_HOLD, len(df))):
        high = float(df["high"].iloc[j])
        low = float(df["low"].iloc[j])
        close = float(df["close"].iloc[j])
        if direction == "BUY":
            if low <= stop:
                return stop_r
            if high >= target:
                return rr
        else:
            if high >= stop:
                return stop_r
            if low <= target:
                return rr
        if be_at_r > 0 and stop_r < 0 and side * (close - entry) >= be_at_r * sl_dist:
            stop, stop_r = entry, 0.0
    return None  # never resolved inside the window


def load_candles(symbol: str, profile: dict) -> pd.DataFrame:
    """Entry-timeframe candles from DATA_DIR when set, otherwise Yahoo."""
    if DATA_DIR:
        path = find_history_file(DATA_DIR, symbol)
        if path is None:
            raise RuntimeError(f"no file for {symbol} in {DATA_DIR}")
        return resample_ohlcv(load_history_file(path), profile["interval"])

    ticker = YF_TICKERS.get(symbol, symbol)
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
    return raw


def run_symbol(symbol: str) -> tuple[Stats, Stats, list[tuple[pd.Timestamp, float]], dict[str, Stats]]:
    profile = PROFILES.get(symbol.upper(), DEFAULT_PROFILE)
    raw = load_candles(symbol, profile)
    if len(raw) <= WARMUP + 1:
        raise RuntimeError(f"only {len(raw)} candles for {symbol}, need more than {WARMUP}")
    df = compute_all(raw, INSTRUMENT_CFG)

    htf_raw = raw.resample(profile["htf"]).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()

    legacy, modern = Stats(), Stats()
    v2_trades: list[tuple[pd.Timestamp, float]] = []
    v2_entries: list[tuple[int, str]] = []
    open_until = {"legacy": 0, "v2": 0}

    for i in range(WARMUP, len(df) - 1):
        window = df.iloc[: i + 1]
        htf_window = htf_raw[htf_raw.index <= df.index[i]]

        if i >= open_until["legacy"]:
            direction = legacy_generate(window, htf_window)
            if direction in ("BUY", "SELL"):
                legacy.record(simulate(df, i, direction, symbol, RISK_LEGACY))
                open_until["legacy"] = i + 6  # one position at a time-ish

        if i >= open_until["v2"]:
            sig = v2.generate_signal(window, SETTINGS, INSTRUMENT_CFG, df_htf=htf_window)
            if sig.direction in ("BUY", "SELL") and REGIME_GATE:
                if regime_block_reason(classify_regime(window)):
                    continue
            if sig.direction in ("BUY", "SELL"):
                r = simulate(df, i, sig.direction, symbol, RISK_V2, be_at_r=BE_AT_R)
                if r is not None:
                    v2_trades.append((df.index[i], r))
                modern.record(r, cost=COST_R)
                v2_entries.append((i, sig.direction))
                open_until["v2"] = i + 6

    # Replay the same entries under each stress variant.
    stress = {label: Stats() for label, *_ in STRESS}
    for label, stop_mult, target_mult, cost_mult in STRESS:
        for i, direction in v2_entries:
            stress[label].record(
                simulate(df, i, direction, symbol, RISK_V2, be_at_r=BE_AT_R,
                         stop_mult=stop_mult, target_mult=target_mult),
                cost=COST_R * cost_mult,
            )

    return legacy, modern, v2_trades, stress


def merge(total: Stats, stats: Stats) -> None:
    total.trades += stats.trades
    total.wins += stats.wins
    total.losses += stats.losses
    total.scratches += stats.scratches
    total.unresolved += stats.unresolved
    total.r_total += stats.r_total


def verdict(v2: Stats, folds: list[Stats], stress: dict[str, Stats]) -> list[tuple[bool, str]]:
    """Pass/fail checks a result must clear before it is believed."""
    resolved = v2.wins + v2.losses + v2.scratches
    positive_folds = sum(1 for f in folds if f.expectancy > 0)
    variants = [s for label, s in stress.items() if label != "baseline"]
    surviving = sum(1 for s in variants if s.expectancy > 0)
    return [
        (resolved >= MIN_TRADES,
         f"sample size: {resolved} resolved trades (minimum {MIN_TRADES}, {GOOD_TRADES}+ to trust it)"),
        (v2.expectancy > 0,
         f"positive after costs: {v2.expectancy:+.2f}R per trade"),
        (bool(folds) and positive_folds > len(folds) / 2,
         f"holds across time: {positive_folds}/{len(folds)} periods profitable"),
        (bool(variants) and surviving == len(variants),
         f"survives stress: {surviving}/{len(variants)} variants still profitable"),
    ]


def main() -> None:
    symbols = sys.argv[1:] or DEFAULT_SYMBOLS
    print(
        f"v2 settings: cost {COST_R}R per trade, regime gate {'on' if REGIME_GATE else 'off'}, "
        f"breakeven stop {'at ' + str(BE_AT_R) + 'R' if BE_AT_R > 0 else 'off'}, "
        f"prices from {DATA_DIR or 'Yahoo'}"
    )
    print(f"{'symbol':<8} {'engine':<7} {'trades':>6} {'wins':>5} {'losses':>6} {'win%':>6} {'sumR':>7} {'exp/R':>6}")
    print("-" * 56)
    totals = {"legacy": Stats(), "v2": Stats()}
    all_v2_trades: list[tuple[pd.Timestamp, float]] = []
    stress_totals = {label: Stats() for label, *_ in STRESS}
    for symbol in symbols:
        try:
            legacy, modern, v2_trades, stress = run_symbol(symbol)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"{symbol:<8} skipped: {exc}")
            continue
        all_v2_trades.extend(v2_trades)
        for label, stats in stress.items():
            merge(stress_totals[label], stats)
        for name, stats in (("legacy", legacy), ("v2", modern)):
            merge(totals[name], stats)
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

    folds = fold_stats(all_v2_trades, FOLDS)
    if len(folds) > 1:
        print()
        print(f"Walk-forward, v2 after costs ({len(folds)} consecutive periods, oldest first)")
        for k, stats in enumerate(folds, 1):
            print(
                f"  period {k}: {stats.trades:>4} trades  {stats.winrate:>5.1f}% win  "
                f"{stats.r_total:>+7.1f}R  {stats.expectancy:>+5.2f}R each"
            )
        positive = sum(1 for stats in folds if stats.expectancy > 0)
        print(f"  profitable periods: {positive}/{len(folds)}")

    if totals["v2"].trades:
        print()
        print("Robustness, v2 (same entries, settings moved)")
        for label, stats in stress_totals.items():
            print(
                f"  {label:<20} {stats.winrate:>5.1f}% win  "
                f"{stats.r_total:>+7.1f}R  {stats.expectancy:>+5.2f}R each"
            )

        print()
        print("Verdict")
        checks = verdict(totals["v2"], folds, stress_totals)
        for ok, text in checks:
            print(f"  {'PASS' if ok else 'FAIL'}  {text}")
        if all(ok for ok, _ in checks):
            print("  All checks pass. This is evidence, not a guarantee.")
        else:
            print("  Do not rely on this result until every check passes.")


if __name__ == "__main__":
    main()
