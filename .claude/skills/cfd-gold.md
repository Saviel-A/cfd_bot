---
name: cfd-gold
description: Professional CFD and Gold (XAUUSD) trading expertise for this bot. Use when working on signal logic, indicator tuning, SL/TP rules, filters, risk management, or any trading strategy decisions. Embeds research-backed rules so you never need to guess.
---

# CFD & Gold (XAUUSD) Trading Expert

You are operating as a professional CFD signal bot developer with deep knowledge of gold (XAU/USD) trading. Apply every rule below when touching signal logic, filters, risk, or strategy.

---

## Bot Architecture

- **Entry timeframe**: 15M candles (XAUUSD uses `GC=F` via yfinance)
- **HTF trend filter**: 1H EMA **20/50** (bias = BULLISH / BEARISH / NEUTRAL) — EMA 50/200 was 8-day lag, too slow for intraday gold
- **Signal engine**: 4 indicators vote +1/−1/0, need **3/4 aligned** with HTF bias
- **SL**: **0.75×ATR**, clamped to **7–10 pts** (1 pt = $1 for gold) — 0.5×ATR always floored to 7pt minimum which is stop-hunt territory when ATR≈11
- **TP**: SL × 2.0 (2:1 minimum risk-reward — confirmed best practice for gold)
- **Session**: Alerts only **10:00–20:00 Israel time** (= London + NY overlap)
- **Counter-trend**: Gold counter-trend signals are **fully blocked** (pressure check)

---

## Indicator Settings (Gold-Specific — Research Confirmed)

| Indicator | Setting | Why |
|---|---|---|
| RSI | Period **9**, neutral zone **48–52** | RSI-9 is standard for intraday gold; 14 is too slow for 15M volatility |
| MACD | **(8, 21, 5)** | Faster than default (12,26,9); reduces lag on 15M |
| EMA | **9 / 21** | Default; EMA 9/21 crossover is sufficient for 15M |
| Bollinger | 20-period, 2 std dev | Standard; price above/below midline votes bullish/bearish |
| ADX | Period 14, threshold **≥ 20** | Block ranging markets; ADX < 20 = no signal |
| ATR | Period 14 | SL sizing base |

---

## Entry Quality Rules (All Must Pass for Gold Alerts)

1. **3/4 indicator confluence** — minimum signal strength
2. **ADX ≥ 20** — market must be trending, not ranging
3. **Candle body ≥ 35% of candle range** — rejects doji/indecision candles; only fire on conviction candles (bullish/bearish engulfing style). Code threshold: `body / range < 0.35`
4. **Volume ≥ 60% of 20-bar average** — rejects low-participation fake breakouts; gold futures volume (GC=F) is reliable during London/NY. Code threshold: `vol < avg_vol * 0.6`
5. **Swing SL gate**: compute distance from last close to swing high/low over last 8 candles; if that distance > 10 pts, **block** (market structure requires too wide a stop)
6. **Session filter**: London + NY only (10:00–20:00 Israel); Asian session has negative expectancy on gold
7. **Pressure alignment**: `buy_pct ≥ 55%` for BUY, `sell_pct ≥ 55%` for SELL; strong opposite pressure (`≥ 60%`) always blocks
8. **Cooldown**: duplicate direction within cooldown window → skip; direction flip within cooldown → warn
9. **News block**: high-impact economic events → no alert
10. **Open trade gate**: if a previous signal is still OPEN, no new alert for same symbol

---

## Stop Loss Placement (Professional Rules)

- **Base**: `0.75 × ATR(14)`, floor 7 pts, ceiling 10 pts
- **Structure gate**: if nearest swing high (SELL) or swing low (BUY) over last 8 candles is > 10 pts away → skip the trade entirely (structure risk is too high)
- **Never widen SL** after entry — predefine and automate
- SL for gold is placed at a structural level, not just a fixed distance

---

## Risk / Reward

- **Minimum RR**: 2:1 (TP = SL × 2.0)
- **Account risk per trade**: 1.5% of balance
- Counter-trend signals use RR 1:1 (but Gold blocks counter-trend entirely)
- Never take a trade where the SL would need to be > 10 pts

---

## What Blocks a Gold Signal (Decision Tree)

```
Signal generated (BUY/SELL)?
  → No clean setup?            → HOLD
  → HTF bias NEUTRAL?          → HOLD
  → Stale candle?              → HOLD
  → Counter-trend?             → HOLD (gold only)
  → Pressure misaligned?       → HOLD
  → ADX < 20?                  → HOLD (ranging)
  → Strength < 3/4?            → HOLD
  → Candle body < 35%?         → HOLD (indecision)
  → Volume < 60% avg?          → HOLD (low conviction)
  → Swing SL > 10pts?          → HOLD (structure risk)
  → Outside session hours?     → No broadcast
  → Open trade exists?         → No broadcast
  → In cooldown?               → No broadcast
  → High-impact news?          → No broadcast
  → All clear → BROADCAST
```

---

## What Not To Do

- **Do NOT** use EMA 21/55 or EMA 50/200 for 15M entries — too slow (55-period EMA = 13+ hours)
- **Do NOT** block trend-following signals with RSI overbought/oversold — RSI only blocks counter-trend
- **Do NOT** use SL_MAX > 10 for gold — 10 pts is already generous for 15M
- **Do NOT** use 1:1 RR for gold trend signals — 2:1 minimum
- **Do NOT** remove the session filter — Asian session signals have negative expectancy
- **Do NOT** skip the volume check — low-volume gold candles produce fake breakouts

---

## Key Files

| File | Role |
|---|---|
| [src/signal_engine.py](src/signal_engine.py) | Core voting logic, HTF bias, counter-trend detection |
| [src/indicators.py](src/indicators.py) | RSI, MACD, EMA, Bollinger, ATR, ADX computation |
| [src/instruments.py](src/instruments.py) | Gold-specific indicator overrides (RSI-9, MACD 8/21/5) |
| [src/risk_manager.py](src/risk_manager.py) | SL/TP calculation, pip sizing |
| [src/market_pressure.py](src/market_pressure.py) | Candle pressure analysis, gold pressure thresholds |
| [src/gold_strategy.py](src/gold_strategy.py) | Gold momentum promotion logic |
| [src/signal_profiles.py](src/signal_profiles.py) | Timeframe config per symbol (15M entry / 1H HTF for gold) |
| [bot/scanner.py](bot/scanner.py) | All quality gates, session filter, swing SL, conviction check |
| [bot/outcome_tracker.py](bot/outcome_tracker.py) | TP/SL detection, 24h signal expiry |
| [bot/config.py](bot/config.py) | SL_MIN=7, SL_MAX=10, RR1=2.0 from .env |

---

## When Making Changes

1. Any indicator tuning → check `src/instruments.py` overrides first
2. Any new filter → add to `_gold_quality_suppression_reason()` in `bot/scanner.py`
3. Any SL/TP change → update `.env` values (SL_MIN, SL_MAX, RR1), not hardcoded values
4. After any code change → `pm2 restart cfd-bot --update-env` (never `pkill`)
5. Check `pm2 logs cfd-bot --lines 20 --nostream` to verify
