"""
Live-gate backtest for Gold — replays history through the EXACT same
decision stack the live scanner runs, not just the bare core engine.

scripts/backtest.py only calls src.signal_engine.generate_signal(), which
is the ~5-signals/day number quoted in bot/config.py. In production,
bot/scanner.py additionally runs every BUY/SELL candidate through 6 more
live-only gates before it's allowed to broadcast:

  1. should_block_by_pressure   (src/market_pressure.py)
  2. apply_gold_momentum        (src/gold_strategy.py) - can ALSO promote
     a HOLD to BUY/SELL, so this isn't purely a suppressor
  3. ADX >= 20 ("Market is ranging")
  4. Gold quality gate (5 sub-checks): candle body >= 28% of range,
     price on the correct side of EMA21, volume >= 60% of 20-bar avg,
     4H trend must not contradict the 1H direction, today's range hasn't
     exceeded the 10-day ADR
  5. Structural swing-SL distance cap
  6. Session window: alerts only 07:00-22:00 UTC (London+NY)

None of these run in scripts/backtest.py. This script imports the real
functions from bot/scanner.py, src/market_pressure.py and
src/gold_strategy.py directly (not reimplemented) so the replay can't
drift from what's actually deployed, and reports:
  - how many BUY/SELL candidates the core engine proposed
  - which gate killed how many of them (first-blocking-reason wins,
    same order as bot/scanner.py)
  - signals/day and win rate for what survives every gate

Usage:
  .venv/bin/python scripts/backtest_live_gold.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

sys.path.insert(0, ".")

from src.indicators import compute_all  # noqa: E402
from src import signal_engine as v2  # noqa: E402
from src.risk_manager import calculate_trade  # noqa: E402
from src.instruments import load_instrument_cfg  # noqa: E402
from src.market_pressure import analyze_market_pressure, should_block_by_pressure  # noqa: E402
from src.gold_strategy import apply_gold_momentum  # noqa: E402
from bot.scanner import (  # noqa: E402
    _adx_suppression_reason,
    _gold_quality_suppression_reason,
    _swing_sl_distance,
)
from src.risk_manager import get_pip_size  # noqa: E402

SYMBOL = "XAUUSD"
TICKER = "GC=F"
INTERVAL = "15m"
PERIOD = "59d"          # yfinance intraday cap
HTF = "1h"
WARMUP = 210
MAX_HOLD = 96

MIN_CONFLUENCE = int(os.getenv("MIN_CONF", "3"))   # live default (bot/config.py)
SIGNAL_CFG = {"signals": {"min_confluence": MIN_CONFLUENCE, "indicators": {}}}
RISK_CFG = {"sl_atr_multiplier": 0.5, "sl_min": 7.0, "sl_max": 10.0, "rr1": 1.5}
SESSION_START_UTC, SESSION_END_UTC = 7, 22
DUPLICATE_COOLDOWN_BARS = 2   # 30 min at 15m, matches signal_profiles.py gold cooldown


@dataclass
class Stats:
    candidates: int = 0
    survived: int = 0
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
    entry = float(df["close"].iloc[i])
    atr = float(df["atr"].iloc[i])
    trade = calculate_trade(direction, entry, atr, risk_cfg, symbol=symbol)
    if trade is None:
        return None
    rr = float(risk_cfg.get("rr1", 1.5))
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
    return None


def main() -> None:
    raw = yf.download(TICKER, period=PERIOD, interval=INTERVAL, progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        raise RuntimeError(f"no data for {SYMBOL} ({TICKER})")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0].lower() for c in raw.columns]
    else:
        raw.columns = [c.lower() for c in raw.columns]

    cfg_inst = load_instrument_cfg(SYMBOL)
    df = compute_all(raw, cfg_inst)

    htf_raw = raw.resample(HTF).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    daily_raw = raw.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()

    reasons: Counter[str] = Counter()
    stats = Stats()
    open_until = 0
    days_covered = (df.index[-1] - df.index[WARMUP]).total_seconds() / 86400

    for i in range(WARMUP, len(df) - 1):
        window = df.iloc[: i + 1]
        ts = df.index[i]
        htf_window = htf_raw[htf_raw.index <= ts]
        daily_window = daily_raw[daily_raw.index <= ts]

        signal = v2.generate_signal(
            window, SIGNAL_CFG, cfg_inst, df_htf=htf_window,
            htf_label="1H", entry_label="15M",
        )

        pressure = analyze_market_pressure(window)
        apply_gold_momentum(SYMBOL, signal, pressure)   # may promote HOLD -> BUY/SELL

        if signal.direction not in ("BUY", "SELL"):
            continue

        stats.candidates += 1

        if i < open_until:
            reasons["pacing: open trade / duplicate cooldown"] += 1
            continue

        hour = ts.tz_convert("UTC").hour if ts.tzinfo else ts.hour
        if not (SESSION_START_UTC <= hour < SESSION_END_UTC):
            reasons["session window (only 07-22 UTC)"] += 1
            continue

        if should_block_by_pressure(SYMBOL, signal, pressure):
            reasons["pressure block"] += 1
            continue

        adx_reason = _adx_suppression_reason(SYMBOL, window)
        if adx_reason:
            reasons["ADX < 20 (ranging)"] += 1
            continue

        atr = float(window["atr"].iloc[-1] or 0)
        quality_reason = _gold_quality_suppression_reason(
            SYMBOL, signal, window, pressure, atr=atr, df_daily=daily_window
        )
        if quality_reason:
            key = quality_reason.split(":", 1)[-1].strip()
            key = key.split("(")[0].strip()
            reasons[f"quality gate: {key}"] += 1
            continue

        swing_dist = _swing_sl_distance(signal.direction, window)
        if swing_dist is not None:
            pip = get_pip_size(SYMBOL)
            sl_max_pts = float(RISK_CFG.get("sl_max", 10)) * 1.8
            swing_pts = swing_dist / pip
            if swing_pts > sl_max_pts:
                reasons["structural SL too wide"] += 1
                continue

        # Survived every live gate -> this is a real broadcastable signal.
        stats.survived += 1
        open_until = i + DUPLICATE_COOLDOWN_BARS
        r = simulate(df, i, signal.direction, SYMBOL, RISK_CFG)
        if r is None:
            stats.unresolved += 1
        elif r > 0:
            stats.wins += 1
            stats.r_total += r
        else:
            stats.losses += 1
            stats.r_total += r

    print(f"Gold live-gate replay — {days_covered:.0f} days, MIN_CONFLUENCE={MIN_CONFLUENCE}")
    print("-" * 64)
    print(f"Core engine candidates (BUY/SELL from generate_signal + momentum promo): {stats.candidates}")
    print(f"  -> {stats.candidates / days_covered:.2f} candidates/day\n")
    print("Killed by (first-blocking-reason wins, live order):")
    for reason, count in reasons.most_common():
        print(f"  {count:>4}  {reason}")
    print()
    print(f"Survived ALL live gates: {stats.survived}  ({stats.survived / days_covered:.2f}/day)")
    print(f"  wins {stats.wins}  losses {stats.losses}  unresolved {stats.unresolved}  "
          f"win% {stats.winrate:.1f}  sumR {stats.r_total:+.1f}  exp/R {stats.expectancy:+.2f}")


if __name__ == "__main__":
    main()
