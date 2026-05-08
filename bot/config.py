"""
Config: deployment secrets from .env, strategy constants hardcoded.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


class Config:
    # Deployment (from .env)
    BOT_TOKEN: str             = os.environ["TELEGRAM_BOT_TOKEN"]
    OWNER_CHAT_ID: int         = int(os.environ["OWNER_CHAT_ID"])
    POSTGRES_URL: str          = os.environ["POSTGRES_URL"]
    BROADCAST_CHANNEL_ID: int | None = (
        int(os.environ["BROADCAST_CHANNEL_ID"])
        if os.getenv("BROADCAST_CHANNEL_ID") else None
    )
    SCAN_INTERVAL_MINUTES: int = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))

    # Strategy (hardcoded)
    DEFAULT_TIMEFRAME: str   = "1h"
    HTF_TIMEFRAME: str       = "4h"
    MIN_CONFLUENCE: int      = 3     # indicators must agree out of 4
    SL_ATR_MULTIPLIER: float = float(os.getenv("SL_ATR_MULTIPLIER", "0.5"))
    SL_MIN: float            = float(os.getenv("SL_MIN", "7.0"))
    SL_MAX: float            = float(os.getenv("SL_MAX", "10.0"))
    RR1: float               = float(os.getenv("RR1", "1.5"))
    RR2: float               = float(os.getenv("RR2", "2.0"))
    RR3: float               = float(os.getenv("RR3", "3.0"))
    ACCOUNT_BALANCE: float   = 10000
    RISK_PERCENT: float      = 1.5


cfg = Config()

# Shared dicts passed to signal engine and risk manager
SIGNAL_CFG = {
    "signals": {
        "min_confluence": cfg.MIN_CONFLUENCE,
        "indicators": {"ema_cross": True, "rsi": True, "macd": True, "bollinger": True, "atr": True},
    }
}

RISK_CFG = {
    "account_balance":   cfg.ACCOUNT_BALANCE,
    "risk_percent":      cfg.RISK_PERCENT,
    "sl_atr_multiplier": cfg.SL_ATR_MULTIPLIER,
    "sl_min":            cfg.SL_MIN,
    "sl_max":            cfg.SL_MAX,
    "rr1":               cfg.RR1,
    "rr2":               cfg.RR2,
    "rr3":               cfg.RR3,
}
