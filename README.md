# CFD Smart Signal Bot

A professional Telegram signal bot for Forex, Gold, Indices and Crypto. Scans the owner's watchlist on a fixed interval, generates BUY/SELL signals using multi-timeframe analysis, and broadcasts them to a private Telegram channel. Users subscribe via Telegram Stars to get access.

## How It Works

1. The bot scans the owner's watchlist every 60 minutes (configurable)
2. Each symbol is analysed on two timeframes — 4H for trend bias, 1H for entry
3. Four indicators vote: EMA cross, RSI, MACD, Bollinger Bands
4. If 2 or more agree AND align with the 4H trend — a signal fires
5. Recent candle pressure must confirm the trade direction
6. Stop Loss and 3 Take Profit levels are calculated using ATR
7. The signal is broadcast to the private channel instantly

Signals only fire on **closed candles** — never on a forming candle.

## Requirements

- Python 3.12+
- PostgreSQL (Supabase or self-hosted)
- Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

### 1. Clone and install

```bash
git clone <your-repo>
cd cfd_bot
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from BotFather |
| `OWNER_CHAT_ID` | Yes | Your Telegram numeric user ID |
| `POSTGRES_URL` | Yes | PostgreSQL connection string |
| `BROADCAST_CHANNEL_ID` | Yes | Private channel ID (e.g. `-1001234567890`) |
| `SCAN_INTERVAL_MINUTES` | No | Scan frequency in minutes (default: 60) |

**Get your `OWNER_CHAT_ID`:** Message [@userinfobot](https://t.me/userinfobot) on Telegram.

**Get your `BROADCAST_CHANNEL_ID`:** Forward any message from your channel to [@userinfobot](https://t.me/userinfobot) — it shows the channel ID.

**Make the bot admin in the channel:** The bot must have "Post Messages" permission in the channel to broadcast signals.

### 3. Run

```bash
.venv/bin/python bot_app.py
```

The bot sends a startup message to the owner on launch.

## Commands

### Owner

| Command | Description |
|---|---|
| `/start` | Owner Console |
| `/help` | Owner Console |
| `/signal XAUUSD` | Check Signal |
| `/scan` | Scan Channel |
| `/chart XAUUSD` | Chart |
| `/stats` | Performance |
| `/history` | History |
| `/watchlist` | Watchlist |
| `/symbols` | Instruments |
| `/add XAUUSD` | Add Symbol |
| `/remove XAUUSD` | Remove Symbol |
| `/market` | News |
| `/calendar` | Calendar |
| `/hours` | Market Hours |
| `/users` | Subscribers |
| `/approve` | Invite Subscriber |

### Subscribers (non-owner)

Subscribers see a subscribe screen when they open the bot. They pay via Telegram Stars for access to the private channel where signals are broadcast.

## Signal Format

```
📈 XAUUSD: Gold BUY
Setup: 4H bullish + 1H confirms

Entry: 2,345.60
SL: 2,337.60  🛑 8 pips
TP1: 2,357.60
TP2: 2,361.60
TP3: 2,369.60

News clear | Buyers 62%
Risk is capped at 7-10 pips. Exit at SL.
```

## Strategy

All strategy parameters are hardcoded — no user configuration needed:

| Parameter | Value |
|---|---|
| Entry timeframe | 1H |
| Trend filter | 4H EMA 50/200 |
| Indicators | EMA cross, RSI, MACD, Bollinger Bands |
| Min confluence | 3 of 4 indicators |
| Counter-trend signals | Only allowed when all 1H indicators override the 4H trend |
| RSI exhaustion filter | Blocks BUY above 70 and SELL below 30 |
| Market pressure filter | Blocks alerts when recent candles do not confirm direction |
| Stop Loss | ATR × 0.5, clamped between 7 and 10 pips |
| TP1 | SL × 1.5 |
| TP2 | SL × 2.0 |
| TP3 | SL × 3.0 |

## Running in the Background

```bash
nohup .venv/bin/python bot_app.py > bot.log 2>&1 &
```

Or with pm2:

```bash
pm2 start ".venv/bin/python bot_app.py" --name cfd-bot
```

## Disclaimer

Signals are for informational purposes only. Not financial advice. Always manage your own risk.
