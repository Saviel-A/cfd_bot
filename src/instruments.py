"""
Instrument registry — maps symbol names to yfinance tickers.
All symbols are resolved from TICKER_MAP. Unknown symbols are tried as raw yfinance tickers.
"""

from typing import Dict

# Format: "SYMBOL": ("yfinance_ticker", "Display Name")

TICKER_MAP: Dict[str, tuple] = {
    # ── Metals ─────────────────────────────────────────────────────
    "XAUUSD":   ("GC=F",      "Gold"),
    "GOLD":     ("GC=F",      "Gold"),
    "XAGUSD":   ("SI=F",      "Silver"),
    "SILVER":   ("SI=F",      "Silver"),
    "XPTUSD":   ("PL=F",      "Platinum"),
    "COPPER":   ("HG=F",      "Copper"),

    # ── Energy ─────────────────────────────────────────────────────
    "USOIL":    ("CL=F",      "WTI Crude Oil"),
    "OIL":      ("CL=F",      "WTI Crude Oil"),
    "WTIUSD":   ("CL=F",      "WTI Crude Oil"),
    "BRENT":    ("BZ=F",      "Brent Crude Oil"),
    "NATGAS":   ("NG=F",      "Natural Gas"),

    # ── US Indices ─────────────────────────────────────────────────
    "US30":     ("^DJI",      "Dow Jones 30"),
    "DJI":      ("^DJI",      "Dow Jones 30"),
    "US500":    ("^GSPC",     "S&P 500"),
    "SPX":      ("^GSPC",     "S&P 500"),
    "SP500":    ("^GSPC",     "S&P 500"),
    "NAS100":   ("^IXIC",     "Nasdaq 100"),
    "NDX":      ("^IXIC",     "Nasdaq 100"),
    "NASDAQ":   ("^IXIC",     "Nasdaq 100"),
    "US2000":   ("^RUT",      "Russell 2000"),
    "VIX":      ("^VIX",      "Volatility Index"),

    # ── European Indices ───────────────────────────────────────────
    "UK100":    ("^FTSE",     "FTSE 100"),
    "FTSE":     ("^FTSE",     "FTSE 100"),
    "GER40":    ("^GDAXI",    "DAX 40"),
    "DAX":      ("^GDAXI",    "DAX 40"),
    "FRA40":    ("^FCHI",     "CAC 40"),
    "CAC":      ("^FCHI",     "CAC 40"),
    "ESP35":    ("^IBEX",     "IBEX 35"),
    "EU50":     ("^STOXX50E", "Euro Stoxx 50"),
    "SWI20":    ("^SSMI",     "Swiss SMI"),

    # ── Asian Indices ──────────────────────────────────────────────
    "JPN225":   ("^N225",     "Nikkei 225"),
    "NIKKEI":   ("^N225",     "Nikkei 225"),
    "HK50":     ("^HSI",      "Hang Seng 50"),
    "AUS200":   ("^AXJO",     "ASX 200"),
    "CHN50":    ("000300.SS", "CSI 300"),

    # ── Major Forex ────────────────────────────────────────────────
    "EURUSD":   ("EURUSD=X",  "EUR/USD"),
    "GBPUSD":   ("GBPUSD=X",  "GBP/USD"),
    "USDJPY":   ("JPY=X",     "USD/JPY"),
    "USDCHF":   ("CHF=X",     "USD/CHF"),
    "AUDUSD":   ("AUDUSD=X",  "AUD/USD"),
    "NZDUSD":   ("NZDUSD=X",  "NZD/USD"),
    "USDCAD":   ("CAD=X",     "USD/CAD"),

    # ── Minor Forex ────────────────────────────────────────────────
    "EURGBP":   ("EURGBP=X",  "EUR/GBP"),
    "EURJPY":   ("EURJPY=X",  "EUR/JPY"),
    "GBPJPY":   ("GBPJPY=X",  "GBP/JPY"),
    "EURCHF":   ("EURCHF=X",  "EUR/CHF"),
    "GBPCHF":   ("GBPCHF=X",  "GBP/CHF"),
    "AUDJPY":   ("AUDJPY=X",  "AUD/JPY"),
    "CADJPY":   ("CADJPY=X",  "CAD/JPY"),
    "CHFJPY":   ("CHFJPY=X",  "CHF/JPY"),
    "AUDCAD":   ("AUDCAD=X",  "AUD/CAD"),
    "AUDCHF":   ("AUDCHF=X",  "AUD/CHF"),
    "CADCHF":   ("CADCHF=X",  "CAD/CHF"),
    "NZDJPY":   ("NZDJPY=X",  "NZD/JPY"),
    "EURAUD":   ("EURAUD=X",  "EUR/AUD"),
    "EURCAD":   ("EURCAD=X",  "EUR/CAD"),
    "EURNZD":   ("EURNZD=X",  "EUR/NZD"),
    "GBPAUD":   ("GBPAUD=X",  "GBP/AUD"),
    "GBPCAD":   ("GBPCAD=X",  "GBP/CAD"),
    "GBPNZD":   ("GBPNZD=X",  "GBP/NZD"),

    # ── Crypto ─────────────────────────────────────────────────────
    "BTC":      ("BTC-USD",   "Bitcoin"),
    "BTCUSD":   ("BTC-USD",   "Bitcoin"),
    "ETH":      ("ETH-USD",   "Ethereum"),
    "ETHUSD":   ("ETH-USD",   "Ethereum"),
    "BNB":      ("BNB-USD",   "BNB"),
    "SOL":      ("SOL-USD",   "Solana"),
    "XRP":      ("XRP-USD",   "Ripple"),
    "ADA":      ("ADA-USD",   "Cardano"),
    "DOGE":     ("DOGE-USD",  "Dogecoin"),
    "AVAX":     ("AVAX-USD",  "Avalanche"),
    "DOT":      ("DOT-USD",   "Polkadot"),
    "MATIC":    ("MATIC-USD", "Polygon"),
    "LINK":     ("LINK-USD",  "Chainlink"),
    "LTC":      ("LTC-USD",   "Litecoin"),

    # ── Popular Stocks ─────────────────────────────────────────────
    "AAPL":     ("AAPL",      "Apple"),
    "TSLA":     ("TSLA",      "Tesla"),
    "NVDA":     ("NVDA",      "NVIDIA"),
    "AMZN":     ("AMZN",      "Amazon"),
    "MSFT":     ("MSFT",      "Microsoft"),
    "GOOGL":    ("GOOGL",     "Alphabet"),
    "META":     ("META",      "Meta"),
    "NFLX":     ("NFLX",      "Netflix"),
    "AMD":      ("AMD",       "AMD"),
    "BABA":     ("BABA",      "Alibaba"),
}

# Categories for /symbols browsing
CATEGORIES = {
    "Metals":        ["XAUUSD", "XAGUSD", "XPTUSD", "COPPER"],
    "Energy":        ["USOIL", "BRENT", "NATGAS"],
    "US Indices":    ["US30", "US500", "NAS100", "US2000"],
    "EU Indices":    ["GER40", "UK100", "FRA40", "EU50"],
    "Asian Indices": ["JPN225", "HK50", "AUS200"],
    "Major Forex":   ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD"],
    "Minor Forex":   ["EURGBP", "EURJPY", "GBPJPY", "EURCHF", "GBPCHF"],
    "Crypto":        ["BTC", "ETH", "SOL", "BNB", "XRP"],
    "Stocks":        ["AAPL", "TSLA", "NVDA", "AMZN", "MSFT"],
}

DEFAULT_CFG = {
    "rsi":       {"period": 14, "oversold": 30, "overbought": 70},
    "ema":       {"fast": 9,  "slow": 21},
    "macd":      {"fast": 12, "slow": 26, "signal": 9},
    "bollinger": {"period": 20, "std_dev": 2.0},
    "atr":       {"period": 14},
}


def resolve_symbol(raw: str) -> tuple[str, str, str]:
    """Return (symbol, ticker, display_name). Unknown symbols are tried as raw yfinance tickers."""
    upper = raw.upper()
    if upper in TICKER_MAP:
        ticker, display = TICKER_MAP[upper]
        return upper, ticker, display
    return upper, raw, raw


def load_instrument_cfg(symbol: str) -> dict:
    _, ticker, display = resolve_symbol(symbol)
    cfg = dict(DEFAULT_CFG)
    cfg["ticker"] = ticker
    cfg["display_name"] = display
    return cfg


def get_ticker_for_symbol(symbol: str) -> str:
    _, ticker, _ = resolve_symbol(symbol)
    return ticker


def get_display_name(symbol: str) -> str:
    _, _, display = resolve_symbol(symbol)
    return display
