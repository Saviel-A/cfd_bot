"""Load price history from files instead of a data vendor.

A CFD is priced by the broker, so the broker's own candles are the most
faithful history to test on. MetaTrader 5 exports them from
View > Symbols > Bars > Export Bars; this reads that file as well as a
plain CSV with time/open/high/low/close columns.
"""

from pathlib import Path

import pandas as pd

OHLC = ["open", "high", "low", "close"]
AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def find_history_file(data_dir: str | Path, symbol: str) -> Path | None:
    """First file in data_dir whose name starts with the symbol (any case)."""
    folder = Path(data_dir)
    if not folder.is_dir():
        return None
    matches = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in (".csv", ".txt")
        and p.stem.upper().startswith(symbol.upper())
    )
    return matches[0] if matches else None


def load_history_file(path: str | Path) -> pd.DataFrame:
    """Read an MT5 bar export or a generic CSV into a UTC-indexed OHLCV frame."""
    raw = pd.read_csv(path, sep=None, engine="python")
    raw.columns = [str(c).strip().strip("<>").lower() for c in raw.columns]

    if "date" in raw.columns and "time" in raw.columns:
        stamp = raw["date"].astype(str) + " " + raw["time"].astype(str)
    else:
        time_col = next(
            (c for c in ("time", "datetime", "date", "timestamp") if c in raw.columns), None
        )
        if time_col is None:
            raise ValueError(f"{path}: no time column found")
        stamp = raw[time_col].astype(str)

    missing = [c for c in OHLC if c not in raw.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")

    volume = next((c for c in ("volume", "tickvol", "vol") if c in raw.columns), None)
    df = pd.DataFrame({c: pd.to_numeric(raw[c], errors="coerce") for c in OHLC})
    df["volume"] = pd.to_numeric(raw[volume], errors="coerce").fillna(0) if volume else 0.0
    df.index = pd.to_datetime(stamp.str.replace(".", "-", regex=False), utc=True)
    df = df[~df.index.isna()].dropna(subset=OHLC)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if df.empty:
        raise ValueError(f"{path}: no usable candles")
    return df


def resample_ohlcv(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Aggregate candles to a coarser interval such as '15m', '1h' or '4h'."""
    rule = interval.replace("m", "min") if interval.endswith("m") else interval
    agg = {k: v for k, v in AGG.items() if k in df.columns}
    return df.resample(rule).agg(agg).dropna(subset=OHLC)
