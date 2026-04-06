
\
# CFD Smart Signal Bot — Project Design Document

## Overview
A professional CFD trading signal bot that monitors financial instruments in real-time, generates buy/sell/hold signals with SL/TP levels, analyses financial news via FinBERT sentiment, and delivers alerts to users via Telegram. Fully configurable from within Telegram — no config files needed by end users.

## Core Principles
- **Signals only** — no trade execution. The bot tells you when to buy/sell, you decide.
- **Closed-candle discipline** — signals are ONLY generated on confirmed closed candles, never on forming candles. This prevents false signals and ensures backtest validity.
- **HTF bias gate** — 4H trend must align with the entry signal from the lower timeframe (1H/15M). Counter-trend signals are discarded.
- **Multi-factor confluence** — minimum score required before a signal fires. No single-indicator signals.
- **Per-user configuration** — every user has their own watchlist, risk settings, timeframe preferences stored in PostgreSQL.

## Architecture

```
Data Layer (yfinance — free, 15min delay, fine for 1H/4H)
    ↓
Signal Engine (multi-timeframe: 4H bias + 1H entry)
    ↓
Strategy Plugins (EMA, RSI, MACD, Bollinger, Structure)
    +
FinBERT News Sentiment (free, runs locally, financial-grade AI)
    ↓
Score-based Confluence Gate (configurable min score)
    ↓
PostgreSQL + TimescaleDB (signals, users, watchlists, history)
    ↓
aiogram 3 Telegram Bot (async, per-user, inline keyboards)
```

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.12+ | Async support, rich ecosystem |
| Async runtime | asyncio | Foundation of everything |
| Telegram bot | aiogram 3 | Async-native, clean API |
| Database | PostgreSQL 16 | Production-grade, reliable |
| Time-series data | TimescaleDB extension | Fast candle queries |
| ORM | SQLAlchemy 2.0 async | Type-safe, async support |
| DB driver | asyncpg | Fastest async PostgreSQL driver |
| Migrations | Alembic | Schema versioning |
| Market data | yfinance | Free, covers all CFD instruments |
| Technical analysis | pandas-ta | Pure Python, easy install |
| News sentiment | FinBERT (HuggingFace) | Financial-grade AI, free, local |
| Data manipulation | pandas + numpy | Standard for quant work |
| Scheduler | APScheduler AsyncIOScheduler | Cron + interval on asyncio loop |
| Config | pydantic-settings + .env | Type-safe environment config |
| Logging | loguru | Structured, coloured logging |
| Deployment | Docker + docker-compose | Reproducible, cross-platform |

## Project Structure

```
cfd_bot/
├── CLAUDE.md                    # This file
├── .env                         # Secrets (never commit)
├── .env.example                 # Template (safe to commit)
├── docker-compose.yml           # PostgreSQL + TimescaleDB
├── requirements.txt
├── pyproject.toml
├── alembic.ini
├── migrations/                  # Alembic migration files
│
├── bot/
│   ├── main.py                  # asyncio entrypoint — starts everything
│   ├── config.py                # pydantic-settings config loader
│   │
│   ├── telegram/
│   │   ├── bot.py               # aiogram dispatcher + handler registration
│   │   ├── broadcaster.py       # Push signals to subscribed users
│   │   ├── formatters.py        # Signal message formatting
│   │   └── handlers/
│   │       ├── start.py         # /start — onboarding wizard
│   │       ├── watchlist.py     # /add /remove /watchlist
│   │       ├── settings.py      # /settings — balance, risk, timeframe
│   │       ├── signals.py       # /signal XAUUSD — on-demand scan
│   │       ├── news.py          # /news XAUUSD — latest news + sentiment
│   │       ├── symbols.py       # /symbols — browse available instruments
│   │       └── callbacks.py     # All inline keyboard button handlers
│   │
│   ├── engine/
│   │   ├── scanner.py           # Main loop — detects candle closes
│   │   ├── signal_engine.py     # Runs strategies, applies confluence gate
│   │   └── multi_timeframe.py   # HTF bias + LTF entry logic
│   │
│   ├── strategies/
│   │   ├── base.py              # Abstract BaseStrategy class
│   │   ├── ema_confluence.py    # EMA 9/21 crossover + trend
│   │   ├── rsi_reversal.py      # RSI oversold/overbought reversals
│   │   ├── macd_momentum.py     # MACD crossover momentum
│   │   ├── bollinger_squeeze.py # Bollinger Band breakout
│   │   └── registry.py          # Strategy loader/registry
│   │
│   ├── news/
│   │   ├── fetcher.py           # Fetch news via yfinance + Google RSS
│   │   ├── sentiment.py         # FinBERT sentiment scoring
│   │   └── cache.py             # News cache (avoid re-fetching)
│   │
│   ├── data/
│   │   ├── fetcher.py           # yfinance OHLCV downloader
│   │   └── candle_store.py      # Save/load candles from PostgreSQL
│   │
│   ├── risk/
│   │   └── manager.py           # ATR-based SL/TP, position sizing
│   │
│   └── db/
│       ├── session.py           # Async engine + session factory
│       ├── models/
│       │   ├── user.py          # User model
│       │   ├── watchlist.py     # User watchlist
│       │   ├── settings.py      # Per-user settings
│       │   ├── signal.py        # Signal history
│       │   └── candle.py        # OHLCV candle storage
│       └── repositories/
│           ├── user_repo.py     # User CRUD
│           ├── watchlist_repo.py
│           ├── settings_repo.py
│           └── signal_repo.py
│
└── config/
    └── instruments/             # Per-symbol ticker configs (YAML)
        ├── XAUUSD.yaml          # Gold → GC=F
        ├── US30.yaml            # Dow Jones → ^DJI
        └── EURUSD.yaml          # EUR/USD → EURUSD=X
```

## Telegram Bot Commands

| Command | Description |
|---|---|
| `/start` | Onboarding wizard with inline keyboard symbol picker |
| `/add XAUUSD` | Add Gold to personal watchlist |
| `/remove XAUUSD` | Remove from watchlist |
| `/watchlist` | Show your symbols + current signal for each |
| `/signal XAUUSD` | Get live signal right now for any symbol |
| `/news XAUUSD` | Latest news + FinBERT sentiment score |
| `/settings` | Interactive menu: balance, risk %, timeframe, SL multiplier |
| `/alerts on` | Enable auto-push alerts when signals fire |
| `/alerts off` | Disable auto-push alerts |
| `/symbols` | Browse all available instruments with inline buttons |
| `/help` | Show all commands |

## Signal Logic

### Multi-Timeframe Analysis
1. **4H candle** → determine HTF bias (bullish/bearish/neutral) via EMA 50/200
2. **1H candle** → look for entry signals aligned with 4H bias
3. If 1H signal contradicts 4H bias → discard

### Indicator Scoring
Each indicator votes with a weighted score:

| Indicator | Max Score | Notes |
|---|---|---|
| HTF EMA bias | +3 | Most important — trend direction |
| RSI zone | +2 | Oversold/overbought confirmation |
| EMA cross (1H) | +2 | Fast/slow crossover |
| MACD momentum | +2 | Momentum confirmation |
| Bollinger position | +1 | Price relative to bands |
| FinBERT news | +2 | Sentiment bonus/penalty |

**Minimum score to fire signal: 7/12**

### SL/TP Calculation
- Stop Loss = ATR × 1.5 (adapts to current volatility)
- Take Profit 1 = SL distance × 1.5 (partial close)
- Take Profit 2 = SL distance × 2.5 (main target)
- Take Profit 3 = SL distance × 4.0 (runner)

## Database Schema

### Key Tables
- `users` — Telegram user registry
- `user_settings` — per-user: balance, risk %, timeframe, alerts on/off
- `user_watchlist` — per-user symbol list
- `signals` — full signal history with entry/SL/TP/outcome
- `signal_deliveries` — audit trail of who received what
- `candles` — TimescaleDB hypertable for OHLCV history

## Adding a New Instrument

1. Create `config/instruments/SYMBOL.yaml` with ticker, display name, and indicator params
2. Add symbol name to `active_instruments` in `.env`
3. Restart the bot — no code changes needed

## Environment Variables (.env)

```
TELEGRAM_BOT_TOKEN=your_token_here
POSTGRES_URL=postgresql+asyncpg://postgres:cfdbot123@localhost:5432/cfdbot
SCAN_INTERVAL_SECONDS=5
MIN_SIGNAL_SCORE=7
DEFAULT_TIMEFRAME=1h
HTF_TIMEFRAME=4h
ACCOUNT_BALANCE=10000
RISK_PERCENT=1.5
SL_ATR_MULTIPLIER=1.5
RISK_REWARD_1=1.5
RISK_REWARD_2=2.5
RISK_REWARD_3=4.0
```

## Key Design Decisions

1. **yfinance over MT5** — No broker account needed. 15-min delay is irrelevant for 1H/4H signals.
2. **FinBERT over Claude API** — Free, runs locally, specifically trained on financial text.
3. **aiogram 3 over python-telegram-bot** — Lighter, faster, cleaner async API.
4. **PostgreSQL over SQLite** — Production-grade. Handles multiple users, concurrent writes, time-series queries.
5. **Closed-candle only** — Signals never fire mid-candle. Prevents false signals and lookahead bias.
6. **Score-based confluence** — No single indicator can trigger a signal. Minimum 7/12 score required.
7. **3 Take Profit levels** — Professional signal format. Allows partial closes at TP1/TP2, runner to TP3.

## Telegram Bot Token
Bot: @cfd_smart_bot
