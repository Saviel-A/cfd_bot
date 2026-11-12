"""Tests for the risk guards, regime, data quality and postmortem modules."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from src.data_quality import ohlcv_quality_issue
from src.postmortem import TradeRecord, build_postmortem, format_postmortem, kelly_fraction, session_of
from src.regime import classify_regime, hurst_exponent, regime_block_reason, volatility_percentile
from src.risk_guard import (
    ClosedTrade, circuit_breaker_reason, correlated_exposure_reason, returns_correlation, trade_r,
)

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)


def candles(closes, spread=0.5):
    closes = np.asarray(closes, dtype=float)
    opens = np.r_[closes[0], closes[:-1]]
    index = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": opens,
        "high": np.maximum(opens, closes) + spread,
        "low": np.minimum(opens, closes) - spread,
        "close": closes,
    }, index=index)


def walk(n=300, seed=1, drift=0.0):
    rng = np.random.default_rng(seed)
    return 100 + np.cumsum(rng.normal(drift, 1.0, n))


# data quality

def test_clean_feed_passes():
    assert ohlcv_quality_issue(candles(walk())) is None


def test_frozen_feed_is_caught():
    closes = np.r_[walk(50), [120.0] * 6]
    assert "frozen" in ohlcv_quality_issue(candles(closes))


def test_missing_and_inconsistent_candles_are_caught():
    df = candles(walk())
    broken = df.copy()
    broken.iloc[-2, broken.columns.get_loc("close")] = np.nan
    assert "missing" in ohlcv_quality_issue(broken)

    broken = df.copy()
    broken.iloc[-3, broken.columns.get_loc("high")] = broken.iloc[-3]["low"] - 1
    assert "inconsistent" in ohlcv_quality_issue(broken)


def test_bad_tick_is_caught():
    df = candles(walk())
    df.iloc[-1, df.columns.get_loc("high")] = df.iloc[-1]["close"] + 500
    assert "bad tick" in ohlcv_quality_issue(df)


def test_empty_feed():
    assert ohlcv_quality_issue(pd.DataFrame()) == "no candles"


# regime

def ar_price(phi, seed, n=400):
    """Price whose steps persist (phi > 0) or reverse (phi < 0)."""
    noise = np.random.default_rng(seed).normal(0, 1, n)
    steps = np.zeros(n)
    for i in range(1, n):
        steps[i] = phi * steps[i - 1] + noise[i]
    return pd.Series(np.exp(np.cumsum(steps) * 0.01))


def test_hurst_separates_trending_from_mean_reverting():
    for seed in range(20):
        assert hurst_exponent(ar_price(0.6, seed)) > 0.56
        assert hurst_exponent(ar_price(-0.6, seed)) < 0.40


def test_hurst_reads_a_random_walk_as_neither():
    values = [hurst_exponent(ar_price(0.0, seed)) for seed in range(40)]
    assert 0.44 < np.mean(values) < 0.52


def test_hurst_has_no_opinion_on_short_history():
    assert hurst_exponent(pd.Series(walk(20))) == 0.5


def test_volatility_percentile():
    assert volatility_percentile(pd.Series(np.linspace(1, 2, 100))) == 100.0
    assert volatility_percentile(pd.Series(np.linspace(2, 1, 100))) == 1.0


def test_shock_and_quiet_regimes_block_entries():
    df = candles(walk())
    df["adx"] = 30.0
    df["atr"] = np.linspace(1, 2, len(df))
    shock = classify_regime(df)
    assert shock.label == "SHOCK"
    assert regime_block_reason(shock)

    df["atr"] = np.linspace(2, 1, len(df))
    assert classify_regime(df).label == "QUIET"


def test_trend_regime_is_allowed():
    df = candles(100 * ar_price(0.7, seed=3, n=300))
    df["adx"] = 30.0
    atr = np.full(len(df), 1.0)
    atr[::2] = 1.2          # current reading sits mid-range
    atr[-1] = 1.0
    df["atr"] = atr
    regime = classify_regime(df)
    assert regime.label == "TREND"
    assert regime_block_reason(regime) is None


# circuit breaker

def closed(symbol, outcome, hours_ago, r=None):
    return ClosedTrade(symbol, outcome, NOW - timedelta(hours=hours_ago),
                       r if r is not None else (-1.0 if outcome == "SL" else 1.5))


def test_trade_r():
    assert trade_r("SL", 100, 95, 110) == -1.0
    assert trade_r("TP", 100, 95, 110) == 2.0
    assert trade_r("TP", 100, 105, 92.5) == 1.5


def test_streak_pauses_only_that_symbol():
    trades = [closed("XAUUSD", "SL", h) for h in (1, 2, 3)]
    assert "3 stop losses in a row" in circuit_breaker_reason("XAUUSD", trades, now=NOW)
    assert circuit_breaker_reason("EURUSD", trades, now=NOW) is None


def test_streak_pause_expires_and_a_win_resets_it():
    old = [closed("XAUUSD", "SL", h) for h in (30, 31, 32)]
    assert circuit_breaker_reason("XAUUSD", old, now=NOW) is None

    broken = [closed("XAUUSD", "SL", 1), closed("XAUUSD", "TP", 2), closed("XAUUSD", "SL", 3),
              closed("XAUUSD", "SL", 4)]
    assert circuit_breaker_reason("XAUUSD", broken, now=NOW) is None


def test_daily_loss_limit_stops_every_symbol():
    trades = [closed(s, "SL", h) for s, h in (("XAUUSD", 1), ("EURUSD", 2), ("GBPUSD", 3), ("NAS100", 4))]
    reason = circuit_breaker_reason("USDJPY", trades, now=NOW)
    assert reason and "-4.0R" in reason


def test_yesterdays_losses_do_not_count_today():
    trades = [closed(s, "SL", h) for s, h in (("XAUUSD", 20), ("EURUSD", 21), ("GBPUSD", 22), ("NAS100", 23))]
    assert circuit_breaker_reason("USDJPY", trades, now=NOW) is None


def test_wins_offset_losses_in_the_daily_limit():
    trades = [closed("EURUSD", "SL", 1), closed("GBPUSD", "SL", 2), closed("NAS100", "SL", 3),
              closed("BTCUSD", "SL", 4), closed("XAUUSD", "TP", 5, r=1.5)]
    assert circuit_breaker_reason("USDJPY", trades, now=NOW) is None


# correlation guard

def correlated_pair(sign=1.0):
    rng = np.random.default_rng(11)
    base = rng.normal(0, 0.002, 200)
    other = sign * base + rng.normal(0, 0.0004, 200)
    index = pd.date_range("2026-01-01", periods=200, freq="1h", tz="UTC")
    return (pd.Series(100 * np.exp(np.cumsum(base)), index=index),
            pd.Series(100 * np.exp(np.cumsum(other)), index=index))


def test_same_direction_on_correlated_pair_is_blocked():
    a, b = correlated_pair()
    closes = {"EURUSD": a, "GBPUSD": b}
    assert returns_correlation(a, b) > 0.9
    assert "same bet" in correlated_exposure_reason("GBPUSD", "BUY", [("EURUSD", "BUY")], closes)
    assert correlated_exposure_reason("GBPUSD", "SELL", [("EURUSD", "BUY")], closes) is None


def test_opposite_direction_on_inverse_pair_is_blocked():
    a, b = correlated_pair(sign=-1.0)
    closes = {"EURUSD": a, "USDCHF": b}
    assert "same bet" in correlated_exposure_reason("USDCHF", "SELL", [("EURUSD", "BUY")], closes)
    assert correlated_exposure_reason("USDCHF", "BUY", [("EURUSD", "BUY")], closes) is None


def test_unrelated_symbols_and_missing_data_pass():
    index = pd.date_range("2026-01-01", periods=200, freq="1h", tz="UTC")
    a = pd.Series(walk(200, seed=1), index=index)
    b = pd.Series(walk(200, seed=2), index=index)
    assert correlated_exposure_reason("NAS100", "BUY", [("XAUUSD", "BUY")], {"NAS100": a, "XAUUSD": b}) is None
    assert correlated_exposure_reason("NAS100", "BUY", [("XAUUSD", "BUY")], {"NAS100": a}) is None
    assert correlated_exposure_reason("NAS100", "BUY", [("XAUUSD", "BUY")], {}) is None


# postmortem

def record(symbol, direction, outcome, hour, strength=3, votes=None):
    return TradeRecord(
        symbol=symbol, direction=direction, outcome=outcome,
        r=1.5 if outcome == "TP" else -1.0,
        fired_at=datetime(2026, 11, 10, hour, 0, tzinfo=timezone.utc),
        strength=strength, votes=votes or {},
    )


def test_kelly_fraction():
    assert round(kelly_fraction(0.5, 1.5), 3) == 0.167
    assert kelly_fraction(0.3, 1.0) < 0


def test_sessions():
    at = lambda h: datetime(2026, 11, 10, h, tzinfo=timezone.utc)
    assert [session_of(at(h)) for h in (3, 8, 13, 18, 22)] == [
        "Asia", "London", "London/NY overlap", "New York", "Asia"]


def test_postmortem_numbers():
    records = [record("XAUUSD", "BUY", "TP", 8)] * 20 + [record("EURUSD", "SELL", "SL", 18)] * 20
    report = build_postmortem(records)
    assert report["trades"] == 40
    assert report["win_rate"] == 50.0
    assert round(report["expectancy"], 2) == 0.25
    assert report["profit_factor"] == 1.5
    assert report["by_symbol"][0].name == "XAUUSD"
    assert report["by_session"][0].name == "London"
    assert report["kelly_pct"] == 16.7
    assert report["suggested_risk_pct"] == 2.0   # quarter Kelly 4.2%, capped
    assert "Suggested" in format_postmortem(report)


def test_postmortem_withholds_sizing_on_small_samples():
    report = build_postmortem([record("XAUUSD", "BUY", "TP", 8), record("XAUUSD", "BUY", "SL", 9)])
    assert report["suggested_risk_pct"] is None
    assert "Needs 30 closed signals" in format_postmortem(report)
    assert "No closed signals" in format_postmortem(build_postmortem([]))


def test_postmortem_factor_breakdown_respects_direction():
    records = [
        record("XAUUSD", "SELL", "TP", 8, votes={"Momentum": -1, "RSI zone": 0}),
        record("XAUUSD", "BUY", "SL", 8, votes={"Momentum": 0, "RSI zone": 1}),
    ]
    names = {b.name: b for b in build_postmortem(records)["by_factor"]}
    assert names["Momentum confirmed"].wins == 1
    assert names["Momentum absent"].wins == 0
    assert names["RSI zone confirmed"].trades == 1


# history files

def test_reads_mt5_export_and_resamples(tmp_path):
    from src.history import find_history_file, load_history_file, resample_ohlcv

    rows = ["<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"]
    for k in range(8):
        price = 2000 + k
        rows.append(f"2026.01.05\t{10 + k // 4:02d}:{15 * (k % 4):02d}:00\t{price}\t{price + 2}\t{price - 1}\t{price + 1}\t100\t0\t20")
    (tmp_path / "XAUUSD_M15.csv").write_text("\n".join(rows))

    path = find_history_file(tmp_path, "xauusd")
    df = load_history_file(path)
    assert len(df) == 8
    assert str(df.index.tz) == "UTC"
    assert df["volume"].iloc[0] == 100

    hourly = resample_ohlcv(df, "1h")
    assert len(hourly) == 2
    first = hourly.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"]) == (2000, 2005, 1999, 2004)
    assert first["volume"] == 400


def test_reads_plain_csv_and_reports_missing_files(tmp_path):
    from src.history import find_history_file, load_history_file

    (tmp_path / "EURUSD.csv").write_text(
        "time,open,high,low,close\n2026-01-05 10:00,1.1,1.2,1.0,1.15\n2026-01-05 11:00,1.15,1.3,1.1,1.2\n"
    )
    df = load_history_file(tmp_path / "EURUSD.csv")
    assert list(df["close"]) == [1.15, 1.2]
    assert find_history_file(tmp_path, "GBPUSD") is None
    assert find_history_file(tmp_path / "nope", "EURUSD") is None
