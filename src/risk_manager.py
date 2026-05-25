"""
Risk Manager — ATR-based SL clamped to a symbol-aware pip range.

TP1 = SL × RR1 (partial close, default 1.5)
TP2 = SL × RR2 (main target,  default 2.0)
TP3 = SL × RR3 (runner,       default 3.0)
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd


PIP_SIZE: dict[str, float] = {
    # Metals
    "XAUUSD": 1.0,
    "XAGUSD": 0.01,
    "XPTUSD": 0.01,
    "COPPER": 0.01,
    # JPY pairs
    "USDJPY": 0.01,
    "EURJPY": 0.01,
    "GBPJPY": 0.01,
    "AUDJPY": 0.01,
    "CADJPY": 0.01,
    "CHFJPY": 0.01,
    "NZDJPY": 0.01,
    # Energy
    "USOIL": 0.01,
    "OIL": 0.01,
    "WTIUSD": 0.01,
    "BRENT": 0.01,
    "NATGAS": 0.001,
    # Indices
    "US30": 1.0,
    "US500": 0.1,
    "SPX": 0.1,
    "SP500": 0.1,
    "NAS100": 0.1,
    "NDX": 0.1,
    "US2000": 0.1,
    "GER40": 0.1,
    "UK100": 0.1,
    "FRA40": 0.1,
    "EU50": 0.1,
    "JPN225": 1.0,
    "HK50": 1.0,
    "AUS200": 0.1,
}

DEFAULT_PIP_SIZE = 0.0001


def get_pip_size(symbol: str = "") -> float:
    return PIP_SIZE.get((symbol or "").upper(), DEFAULT_PIP_SIZE)


@dataclass
class TradeParams:
    direction: str
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    sl_distance: float
    position_size: float
    risk_amount: float
    rr2: float
    atr: float


def calculate_trade(
    direction: str,
    entry_price: float,
    atr: float,
    risk_cfg: dict,
    symbol: str = "",
) -> Optional[TradeParams]:
    if direction not in ("BUY", "SELL"):
        return None
    if atr is None or pd.isna(atr) or atr <= 0:
        return None

    sl_mult  = float(risk_cfg.get("sl_atr_multiplier", 1.5))
    rr1      = float(risk_cfg.get("rr1", 1.5))
    rr2      = float(risk_cfg.get("rr2", 2.5))
    rr3      = float(risk_cfg.get("rr3", 4.0))
    balance  = float(risk_cfg.get("account_balance", 10000))
    risk_pct = float(risk_cfg.get("risk_percent", 1.5))

    sl_dist = atr * sl_mult

    # Clamp SL distance between sl_min and sl_max pips if configured.
    pip_size = get_pip_size(symbol)
    sl_min = risk_cfg.get("sl_min")
    sl_max = risk_cfg.get("sl_max")
    if sl_min is not None:
        sl_dist = max(sl_dist, float(sl_min) * pip_size)
    if sl_max is not None:
        sl_dist = min(sl_dist, float(sl_max) * pip_size)
    risk_amount = balance * (risk_pct / 100)
    position_size = risk_amount / sl_dist if sl_dist > 0 else 0

    if direction == "BUY":
        stop_loss = entry_price - sl_dist
        tp1 = entry_price + sl_dist * rr1
        tp2 = entry_price + sl_dist * rr2
        tp3 = entry_price + sl_dist * rr3
    else:
        stop_loss = entry_price + sl_dist
        tp1 = entry_price - sl_dist * rr1
        tp2 = entry_price - sl_dist * rr2
        tp3 = entry_price - sl_dist * rr3

    return TradeParams(
        direction=direction,
        entry_price=round(entry_price, 5),
        stop_loss=round(stop_loss, 5),
        tp1=round(tp1, 5),
        tp2=round(tp2, 5),
        tp3=round(tp3, 5),
        sl_distance=round(sl_dist, 5),
        position_size=round(position_size, 4),
        risk_amount=round(risk_amount, 2),
        rr2=rr2,
        atr=round(atr, 5),
    )
