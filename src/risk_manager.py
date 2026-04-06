"""
Risk Manager — ATR-based SL + 3 Take Profit levels.

TP1 = SL × RR1 (partial close, default 1.5)
TP2 = SL × RR2 (main target,  default 2.5)
TP3 = SL × RR3 (runner,       default 4.0)
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd


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

    sl_dist      = atr * sl_mult
    risk_amount  = balance * (risk_pct / 100)
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


