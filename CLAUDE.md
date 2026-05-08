# CFD Smart Signal Bot — Project Reference

## What This Bot Does

A professional CFD trading signal bot. The owner manages a watchlist of symbols. The bot scans them on a fixed interval, generates BUY/SELL signals using multi-timeframe analysis, and broadcasts them to a private Telegram channel. Non-owner users pay via Telegram Stars to join the channel.

## Architecture

```
yfinance (market data)
    ↓
Signal Engine (4H bias + 1H entry + 4 indicators)
    ↓
Confluence Gate (2 of 4 indicators must agree + HTF alignment)
    ↓
ATR-based SL/TP calculation
    ↓
Broadcast to private Telegram channel
```

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12+ |
| Telegram bot | aiogram 3 |
| Database | PostgreSQL (Supabase) |
| ORM | SQLAlchemy 2.0 async |
| DB driver | asyncpg |
| Market data | yfinance |
| Config | python-dotenv |

## Project Structure

```
cfd_bot/
├── bot_app.py                   # Entry point
├── .env                         # Secrets (never commit)
├── .env.example                 # Template
│
├── bot/
│   ├── config.py                # Deployment config (.env) + hardcoded strategy constants
│   ├── handlers.py              # All Telegram handlers and callbacks
│   ├── admin.py                 # /users and /approve (owner only)
│   ├── scanner.py               # Scan loop — runs every N minutes
│   ├── outcome_tracker.py       # Checks open signals for TP/SL hits every 15m
│   ├── formatter.py             # Message formatting
│   │
│   └── db/
│       ├── session.py
│       ├── models/
│       │   ├── user.py          # User, premium_until
│       │   ├── settings.py      # UserSettings (created per user, not actively read)
│       │   ├── watchlist.py     # UserWatchlist
│       │   └── signal.py        # Signal history
│       └── repositories/
│           ├── user_repo.py
│           ├── watchlist_repo.py
│           ├── settings_repo.py
│           └── signal_repo.py
│
└── src/
    ├── instruments.py           # Symbol registry + ticker mapping
    ├── data_fetcher.py          # yfinance OHLCV downloader
    ├── indicators.py            # EMA, RSI, MACD, Bollinger, ATR
    ├── signal_engine.py         # Multi-timeframe confluence logic
    ├── risk_manager.py          # ATR-based SL/TP calculation
    ├── trading_hours.py         # Market session hours (Israel time)
    ├── calendar.py              # Forex Factory economic calendar
    └── news.py                  # yfinance news fetcher
```

## Bot Commands

### Owner
| Command | Description |
|---|---|
| `/start` or `/help` | Main menu |
| `/watchlist` | View symbols and current signals |
| `/add SYMBOL` | Add symbol to watchlist |
| `/remove SYMBOL` | Remove symbol |
| `/signal SYMBOL` | On-demand signal |
| `/market` | News + economic calendar |
| `/hours` | Market session times (Israel) |
| `/history` | Last 20 signals |
| `/symbols` | Browse all 80+ instruments |
| `/users` | List users who messaged the bot |
| `/approve ID` | Send channel invite to a user |

### Users (non-owner)
Subscribe via Telegram Stars — receive a single-use channel invite link instantly after payment.

## Strategy (Hardcoded in config.py)

| Parameter | Value |
|---|---|
| Entry timeframe | 1H |
| HTF trend filter | 4H EMA 50/200 |
| Min confluence | 2 of 4 indicators |
| Indicators | EMA cross, RSI, MACD, Bollinger |
| Stop Loss | ATR x 1.5 |
| TP1 / TP2 / TP3 | SL x 1.5 / 2.5 / 4.0 |

Counter-trend signals are discarded. Signals only fire on closed candles.

## Environment Variables (.env)

Only 5 — everything else is hardcoded in config.py:

```
TELEGRAM_BOT_TOKEN=
OWNER_CHAT_ID=
POSTGRES_URL=
BROADCAST_CHANNEL_ID=
SCAN_INTERVAL_MINUTES=60
```

## Signal Flow

1. Scanner fetches 1H and 4H OHLCV data per watchlist symbol
2. 4H EMA 50/200 determines trend bias (BULLISH / BEARISH / NEUTRAL)
3. 4 indicators vote on 1H data
4. If 2+ agree AND align with 4H bias — signal fires
5. ATR-based SL and 3 TP levels calculated
6. Signal saved to DB, broadcast to channel
7. Outcome tracker checks every 15 min if TP/SL was hit, posts result to channel

## Subscription Plans (Telegram Stars)

| Plan | Stars | USD |
|---|---|---|
| 1 Month | 7,600 | ~$99 |
| 3 Months | 15,300 | ~$199 |
| Lifetime | 23,000 | ~$299 |

Payment handled entirely by Telegram. No external gateway needed.
