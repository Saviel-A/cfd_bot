# CFD Smart Signal Bot

A professional Telegram bot that scans financial instruments and delivers buy/sell signals with entry, stop loss, and 3 take profit levels. Signals are generated using multi-timeframe analysis (4H bias + 1H entry) and require confluence from multiple indicators before firing.

## Features

- Multi-timeframe signals — 4H trend filter, 1H entry signals
- 3 take profit levels with ATR-based stop loss
- 80+ instruments — Metals, Forex, Indices, Crypto, Stocks
- Automatic outcome tracking — TP/SL hit detection every 15 minutes
- Per-user settings — watchlist, timeframe, risk, balance
- English and Hebrew language support
- Private access control — approve/revoke users via Telegram
- Optional broadcast channel support

## Requirements

- Python 3.10+
- PostgreSQL database (Supabase or self-hosted)
- Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <your-repo>
cd cfd_bot
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
OWNER_CHAT_ID=your_telegram_user_id
POSTGRES_URL=postgresql+asyncpg://user:password@host:5432/dbname
SCAN_INTERVAL_MINUTES=60
BROADCAST_CHANNEL_ID=        # optional — leave empty if not using a channel
```

**How to get your `OWNER_CHAT_ID`:** Message [@userinfobot](https://t.me/userinfobot) on Telegram — it will reply with your numeric user ID.

### 3. Set up the database

The bot uses PostgreSQL. All tables are created automatically on first run.

If you are using **Supabase**:
- Create a new project at [supabase.com](https://supabase.com)
- Copy the connection string from **Project Settings → Database → Connection string (URI)**
- Replace `[YOUR-PASSWORD]` with your database password
- Change `postgresql://` to `postgresql+asyncpg://`

### 4. Run the bot

```bash
.venv/bin/python bot_app.py
```

You should receive a confirmation message in Telegram: `✅ CFD Signal Bot is online.`

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Show the main menu |
| `/signal XAUUSD` | Get a live signal for any symbol |
| `/watchlist` | See all your symbols and current signals |
| `/add XAUUSD` | Add a symbol to your watchlist |
| `/remove XAUUSD` | Remove a symbol |
| `/symbols` | Browse 80+ instruments by category |
| `/news XAUUSD` | Latest news for a symbol |
| `/history` | Your last 10 received signals |
| `/performance` | Win rate and signal stats |
| `/alerts on\|off` | Enable or disable automatic alerts |
| `/timeframe 1h` | Change scan timeframe |
| `/confluence 3` | Minimum indicators that must agree (1–4) |
| `/balance 10000` | Set your account balance for position sizing |
| `/risk 1.5` | Set risk percentage per trade |
| `/language` | Switch between English and Hebrew |
| `/settings` | View all current settings |

## Admin Commands (Owner Only)

| Command | Description |
|---|---|
| `/users` | List all registered users |
| `/approve USER_ID` | Grant access to a user |
| `/revoke USER_ID` | Remove access from a user |
| `/broadcast message` | Send a message to all active users |

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from BotFather |
| `OWNER_CHAT_ID` | Yes | — | Your Telegram user ID |
| `POSTGRES_URL` | Yes | — | PostgreSQL connection string |
| `SCAN_INTERVAL_MINUTES` | No | `60` | How often to scan watchlists |
| `BROADCAST_CHANNEL_ID` | No | — | Channel ID to broadcast signals to |
| `DEFAULT_TIMEFRAME` | No | `1h` | Default entry timeframe |
| `HTF_TIMEFRAME` | No | `4h` | Higher timeframe for bias filter |
| `ACCOUNT_BALANCE` | No | `10000` | Default account balance |
| `RISK_PERCENT` | No | `1.5` | Default risk per trade (%) |
| `SL_ATR_MULTIPLIER` | No | `1.5` | ATR multiplier for stop loss |

## Signal Logic

1. **4H candle** — determines trend bias via EMA 50/200
2. **1H candle** — checks for entry signals aligned with 4H bias
3. Counter-trend signals are discarded
4. A minimum of 3 out of 4 indicators must agree before a signal fires
5. Stop Loss = ATR × 1.5, TP1 = 1.5R, TP2 = 2.5R, TP3 = 4.0R

Signals are only generated on **closed candles** — never on a forming candle.

## Adding Symbols

Any symbol from yfinance can be added dynamically via `/add`. The bot supports 80+ pre-configured instruments across Metals, Energy, Indices, Forex, Crypto, and Stocks.

To add a custom symbol not in the pre-configured list, just use `/add TICKER` where `TICKER` is a valid yfinance ticker (e.g. `AAPL`, `GC=F`).

## Running in the Background (Linux/Mac)

```bash
nohup .venv/bin/python bot_app.py > bot.log 2>&1 &
```

Or with a process manager like `pm2`:

```bash
pm2 start "source .venv/bin/activate && python bot_app.py" --name cfd-bot
```

## Disclaimer

This bot provides trading signals for informational purposes only. It is not financial advice. Always manage your own risk.
