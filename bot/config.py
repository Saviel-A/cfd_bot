"""
Config — loads settings from .env using pydantic-settings.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


class Config:
    BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
    OWNER_CHAT_ID: int = int(os.environ["OWNER_CHAT_ID"])
    POSTGRES_URL: str = os.environ["POSTGRES_URL"]
    SCAN_INTERVAL_MINUTES: int = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))
    DEFAULT_TIMEFRAME: str = os.getenv("DEFAULT_TIMEFRAME", "1h")
    HTF_TIMEFRAME: str = os.getenv("HTF_TIMEFRAME", "4h")
    ACCOUNT_BALANCE: float = float(os.getenv("ACCOUNT_BALANCE", "10000"))
    RISK_PERCENT: float = float(os.getenv("RISK_PERCENT", "1.5"))
    SL_ATR_MULTIPLIER: float = float(os.getenv("SL_ATR_MULTIPLIER", "1.5"))
    RR1: float = float(os.getenv("RR1", "1.5"))
    RR2: float = float(os.getenv("RR2", "2.5"))
    RR3: float = float(os.getenv("RR3", "4.0"))
    BROADCAST_CHANNEL_ID: int | None = int(os.getenv("BROADCAST_CHANNEL_ID")) if os.getenv("BROADCAST_CHANNEL_ID") else None


cfg = Config()
