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
| Market data | yfinance; Twelve Data for spot gold (optional key) |
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
│   ├── admin.py                 # /users (owner only)
│   ├── scanner.py               # Scan loop — runs every N minutes
│   ├── outcome_tracker.py       # Checks open signals for TP/SL hits every 15m
│   ├── formatter.py             # Message formatting
│   │
│   └── db/
│       ├── session.py
│       ├── models/
│       │   ├── user.py          # User, premium_until
│       │   ├── watchlist.py     # UserWatchlist
│       │   └── signal.py        # Signal history
│       └── repositories/
│           ├── user_repo.py
│           ├── watchlist_repo.py
│           └── signal_repo.py
│
└── src/
    ├── instruments.py           # Symbol registry + ticker mapping
    ├── data_fetcher.py          # yfinance OHLCV downloader (gold from Twelve Data when a key is set)
    ├── twelvedata.py            # Spot XAU/USD candles from Twelve Data
    ├── indicators.py            # EMA, RSI, MACD, Bollinger, ATR
    ├── signal_engine.py         # Multi-timeframe confluence logic
    ├── risk_manager.py          # ATR-based SL/TP calculation
    ├── risk_guard.py            # Circuit breaker (losing streak / daily loss) + correlation guard
    ├── regime.py                # Market regime: Hurst exponent, volatility percentile, ADX
    ├── data_quality.py          # Candle feed sanity checks before a signal is trusted
    ├── history.py               # Reads MetaTrader 5 bar exports / CSV price history for the backtest
    ├── postmortem.py            # Closed-signal breakdown in R + Kelly risk suggestion (/postmortem)
    ├── trading_hours.py         # Market open/closed status per symbol
    └── calendar.py              # Forex Factory calendar: blocks alerts around high-impact news
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
| `/stats` | Win rate and results, with buttons for the breakdown in R and recent alerts |
| `/scan` | Scan the watchlist now |
| `/users` | Subscribers: invite, kick, delete |

The main menu also has buttons for the instrument browser (Add Symbol), inviting a subscriber and the share link.

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

## Risk Guards And Research Tools

Added from ideas in the tradermonty and agiprolabs `claude-trading-skills` repos.
All logic lives in pure modules under `src/` and is covered by `tests/` (`python -m pytest tests`).

| Piece | Default | What it does |
|---|---|---|
| Circuit breaker | on (`CIRCUIT_BREAKER`) | No new alert on a symbol for 6h after 3 stop losses in a row; no new alerts for the rest of the UTC day once the day is at -4R |
| Correlation guard | on (`CORRELATION_GUARD`) | Skips an alert that is the same bet as an open trade (1H return correlation of 0.80 or more) |
| Data quality check | always on | Frozen, missing, inconsistent or bad-tick candles turn the scan into a HOLD |
| Regime gate | off (`REGIME_GATE`) | Skips entries in range / quiet / shock regimes. It changes which entries fire, so prove it with the backtest first |
| Stats > Breakdown | owner button | Win rate and expectancy in R by symbol, direction, confluence, session and factor, plus a quarter-Kelly risk suggestion |
| `scripts/backtest.py` | manual | Charges a cost per trade (`COST_R`), reports walk-forward periods (`FOLDS`), replays entries with stop/target moved and costs doubled, and prints a pass/fail verdict. `DATA_DIR=<folder>` runs it on MetaTrader 5 bar exports (`XAUUSD.csv`, ...) instead of Yahoo. Can test the regime gate (`REGIME_GATE=1`) and a breakeven stop (`BE_AT_R=1.0`) |

## Environment Variables (.env)

5 required, plus optional `TWELVE_DATA_API_KEY` (spot gold candles instead of Yahoo gold futures) and the guard switches in `.env.example`:

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
