"""
Risk Manager – calculates Stop Loss, Take Profit, and position size.

Uses ATR-based SL placement which adapts to current market volatility.
This is far superior to fixed pip stops for CFD trading.

Formula:
  SL distance = ATR × sl_atr_multiplier
  TP distance = SL distance × risk_reward_ratio
  Position size = (account_balance × risk_percent) / SL distance
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class TradeParams:
    direction: str          # BUY or SELL
    entry_price: float
    stop_loss: float
    take_profit: float
    sl_distance: float
    tp_distance: float
    position_size: float    # units / lots (informational)
    risk_amount: float      # $ at risk
    risk_reward: float
    atr: float


def calculate_trade(
    direction: str,
    entry_price: float,
    atr: float,
    risk_cfg: dict,
) -> Optional[TradeParams]:
    """
    Calculate SL, TP and position size for a trade.

    direction   – "BUY" or "SELL"
    entry_price – current market price
    atr         – current ATR value
    risk_cfg    – risk section from settings.yaml
    """
    if direction not in ("BUY", "SELL"):
        return None

    if atr is None or pd.isna(atr) or atr <= 0:
        return None

    sl_mult = risk_cfg.get("sl_atr_multiplier", 1.5)
    rr_ratio = risk_cfg.get("risk_reward_ratio", 2.0)
    balance = risk_cfg.get("account_balance", 10000)
    risk_pct = risk_cfg.get("account_risk_percent", 1.5)

    sl_dist = atr * sl_mult
    tp_dist = sl_dist * rr_ratio
    risk_amount = balance * (risk_pct / 100)

    # Position size = how many units to trade so that hitting SL = risk_amount
    position_size = risk_amount / sl_dist if sl_dist > 0 else 0

    if direction == "BUY":
        stop_loss = entry_price - sl_dist
        take_profit = entry_price + tp_dist
    else:  # SELL
        stop_loss = entry_price + sl_dist
        take_profit = entry_price - tp_dist

    return TradeParams(
        direction=direction,
        entry_price=entry_price,
        stop_loss=round(stop_loss, 5),
        take_profit=round(take_profit, 5),
        sl_distance=round(sl_dist, 5),
        tp_distance=round(tp_dist, 5),
        position_size=round(position_size, 4),
        risk_amount=round(risk_amount, 2),
        risk_reward=rr_ratio,
        atr=round(atr, 5),
    )


def format_trade(trade: TradeParams, display_name: str) -> str:
    """Return a formatted trade card string."""
    arrow = "↑" if trade.direction == "BUY" else "↓"
    lines = [
        f"+-- TRADE SIGNAL -----------------------------------+",
        f"|  Instrument : {display_name}",
        f"|  Direction  : {arrow} {trade.direction}",
        f"|  Entry      : {trade.entry_price:.5f}",
        f"|  Stop Loss  : {trade.stop_loss:.5f}  (-{trade.sl_distance:.5f})",
        f"|  Take Profit: {trade.take_profit:.5f}  (+{trade.tp_distance:.5f})",
        f"|  R:R Ratio  : 1:{trade.risk_reward}",
        f"|  ATR        : {trade.atr:.5f}",
        f"|  Risk $     : ${trade.risk_amount:.2f}",
        f"|  Est. Size  : {trade.position_size:.4f} units",
        f"+---------------------------------------------------+",
    ]
    return "\n".join(lines)
