"""
CFD Bot – Main Entry Point

Usage:
  python run.py              # scan all instruments once and exit
  python run.py --loop       # scan on schedule (uses scan_interval_minutes from settings)
  python run.py --symbol XAUUSD   # scan one instrument only
  python run.py --once --symbol EURUSD  # alias for single scan
"""

import sys
import os
import time
import argparse
import yaml
import schedule
import traceback

# Make sure src/ is importable
sys.path.insert(0, os.path.dirname(__file__))

from src.data_fetcher import fetch_ohlcv
from src.indicators import compute_all
from src.signal_engine import generate_signal, signal_summary
from src.risk_manager import calculate_trade, format_trade
from src import notifier

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.yaml")
INSTRUMENTS_DIR = os.path.join(CONFIG_DIR, "instruments")


def load_settings() -> dict:
    with open(SETTINGS_FILE, "r") as f:
        return yaml.safe_load(f)


def load_instrument(name: str) -> dict:
    path = os.path.join(INSTRUMENTS_DIR, f"{name}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No config found for instrument '{name}' at {path}")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def scan_instrument(name: str, settings: dict):
    """Fetch data, compute indicators, generate signal and print results."""
    try:
        cfg = load_instrument(name)
        ticker = cfg["ticker"]
        display = cfg.get("display_name", name)
        timeframe = cfg.get("timeframe", settings.get("default_timeframe", "1h"))
        lookback = settings.get("candle_lookback", 200)

        notifier.console_info(f"Scanning {display} ({ticker}) on {timeframe}…")

        df = fetch_ohlcv(ticker, timeframe=timeframe, lookback=lookback)
        df = compute_all(df, cfg)

        signal = generate_signal(df, settings, cfg)
        latest = df.iloc[-1]
        atr = float(latest.get("atr", 0)) if latest.get("atr") is not None else 0

        # Only calculate trade params for actionable signals
        trade = None
        if signal.direction in ("BUY", "SELL"):
            trade = calculate_trade(
                direction=signal.direction,
                entry_price=signal.current_price,
                atr=atr,
                risk_cfg=settings.get("risk", {}),
            )

        # ── Console output ────────────────────────────────────────────────
        summary = signal_summary(signal, display)
        notifier.console_signal(signal.direction, summary)

        # Show indicator breakdown
        vote_line = " | ".join(
            f"{k}: {'▲' if v == 1 else ('▼' if v == -1 else '–')}"
            for k, v in signal.details.items()
        )
        notifier.console_info(f"  Indicators → {vote_line}")
        notifier.console_info(f"  RSI: {latest.get('rsi', 0):.1f} | ATR: {atr:.5f}")

        if trade:
            print(format_trade(trade, display))
        elif signal.direction == "HOLD":
            notifier.console_info(f"  No trade – waiting for stronger confluence.")

        # ── Telegram notification ─────────────────────────────────────────
        if signal.direction in ("BUY", "SELL"):
            tg_msg = notifier.build_telegram_message(display, signal, trade)
            notifier.notify(
                direction=signal.direction,
                console_msg="",           # already printed above
                telegram_msg=tg_msg,
                notif_cfg=settings.get("notifications", {}),
            )

        print()

    except FileNotFoundError as e:
        notifier.console_error(str(e))
    except ConnectionError as e:
        notifier.console_error(f"Data error for {name}: {e}")
    except Exception as e:
        notifier.console_error(f"Unexpected error scanning {name}: {e}")
        if "--debug" in sys.argv:
            traceback.print_exc()


def scan_all(settings: dict, filter_symbol: str = None):
    """Scan all active instruments (or one if filter_symbol is set)."""
    instruments = settings.get("active_instruments", [])
    if filter_symbol:
        instruments = [s for s in instruments if s.upper() == filter_symbol.upper()]
        if not instruments:
            notifier.console_warn(
                f"Symbol '{filter_symbol}' not in active_instruments list in settings.yaml"
            )
            return

    notifier.console_info(f"=== CFD Bot Scan === ({len(instruments)} instrument(s))")
    for name in instruments:
        scan_instrument(name, settings)


def main():
    parser = argparse.ArgumentParser(description="CFD Trading Signal Bot")
    parser.add_argument("--loop",   action="store_true", help="Run on schedule continuously")
    parser.add_argument("--symbol", type=str,            help="Scan a single symbol (e.g. XAUUSD)")
    parser.add_argument("--debug",  action="store_true", help="Show full tracebacks on errors")
    args = parser.parse_args()

    settings = load_settings()

    if args.loop:
        interval = settings.get("scan_interval_minutes", 60)
        notifier.console_info(f"Loop mode ON – scanning every {interval} minute(s). Press Ctrl+C to stop.")

        # Run immediately on start
        scan_all(settings, filter_symbol=args.symbol)

        schedule.every(interval).minutes.do(scan_all, settings=settings, filter_symbol=args.symbol)
        try:
            while True:
                schedule.run_pending()
                time.sleep(30)
        except KeyboardInterrupt:
            notifier.console_info("Bot stopped.")
    else:
        scan_all(settings, filter_symbol=args.symbol)


if __name__ == "__main__":
    main()
