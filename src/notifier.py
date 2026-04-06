"""
Notifier – sends signals to console and optionally Telegram.

To enable Telegram:
  1. Create a bot via @BotFather on Telegram → get bot_token
  2. Start a chat with your bot → get chat_id
  3. Update config/settings.yaml → notifications.telegram
"""

import requests
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

# Fix Windows console encoding
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Colour scheme
COLOUR = {
    "BUY":  Fore.GREEN,
    "SELL": Fore.RED,
    "HOLD": Fore.YELLOW,
    "INFO": Fore.CYAN,
    "WARN": Fore.YELLOW,
    "ERR":  Fore.RED,
}


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def console_signal(direction: str, message: str):
    """Print a coloured signal message."""
    colour = COLOUR.get(direction, Fore.WHITE)
    print(f"{colour}[{_timestamp()}] {message}{Style.RESET_ALL}")


def console_info(message: str):
    print(f"{COLOUR['INFO']}[{_timestamp()}] {message}{Style.RESET_ALL}")


def console_warn(message: str):
    print(f"{COLOUR['WARN']}[{_timestamp()}] ⚠  {message}{Style.RESET_ALL}")


def console_error(message: str):
    print(f"{COLOUR['ERR']}[{_timestamp()}] ✗  {message}{Style.RESET_ALL}")


def send_telegram(message: str, token: str, chat_id: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        console_error(f"Telegram send failed: {e}")
        return False


def notify(direction: str, console_msg: str, telegram_msg: str, notif_cfg: dict):
    """
    Send notification to all enabled channels.

    direction     – BUY / SELL / HOLD (for console colouring)
    console_msg   – plain text for terminal
    telegram_msg  – HTML-formatted text for Telegram
    notif_cfg     – notifications section from settings.yaml
    """
    if notif_cfg.get("console", True):
        console_signal(direction, console_msg)

    tg = notif_cfg.get("telegram", {})
    if tg.get("enabled", False):
        token = tg.get("bot_token", "")
        chat_id = tg.get("chat_id", "")
        if token and chat_id and "YOUR_" not in token:
            ok = send_telegram(telegram_msg, token, chat_id)
            if not ok:
                console_warn("Telegram notification failed.")
        else:
            console_warn("Telegram enabled but token/chat_id not configured.")


def build_telegram_message(display_name: str, signal, trade) -> str:
    """Build an HTML-formatted Telegram message."""
    arrow = "🟢" if signal.direction == "BUY" else ("🔴" if signal.direction == "SELL" else "🟡")
    lines = [
        f"<b>{arrow} {signal.direction} – {display_name}</b>",
        f"🕐 {signal.candle_time}",
        f"💰 Entry: <code>{signal.current_price:.5f}</code>",
    ]
    if trade:
        lines += [
            f"🛑 Stop Loss:   <code>{trade.stop_loss:.5f}</code>",
            f"🎯 Take Profit: <code>{trade.take_profit:.5f}</code>",
            f"⚖️  R:R  1:{trade.risk_reward}",
            f"💵 Risk: ${trade.risk_amount:.2f}",
        ]
    lines.append(f"📊 Confluence: {signal.strength}/{signal.total_indicators} indicators")

    votes_str = "  ".join(
        f"{'✅' if v == 1 else ('❌' if v == -1 else '➖')} {k}"
        for k, v in signal.details.items()
    )
    lines.append(f"\n{votes_str}")
    return "\n".join(lines)
