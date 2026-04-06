"""News fetcher — uses yfinance to pull recent headlines for a symbol."""

import logging
import yfinance as yf
from src.instruments import get_ticker_for_symbol

logger = logging.getLogger(__name__)


def get_news(symbol: str, limit: int = 5) -> list[dict]:
    """Return up to `limit` recent news items for a symbol."""
    try:
        ticker = get_ticker_for_symbol(symbol)
        t = yf.Ticker(ticker)
        news = t.news or []
        return news[:limit]
    except Exception as e:
        logger.error(f"Failed to fetch news for {symbol}: {e}")
        return []


def format_news_message(symbol: str, news: list[dict]) -> str:
    if not news:
        return f"<b>{symbol}</b>\n\nNo recent news found."

    lines = [f"<b>{symbol} — Latest News</b>"]
    for item in news:
        title     = item.get("title", "No title")
        publisher = item.get("publisher", "")
        link      = item.get("link") or item.get("url", "")

        if link:
            lines.append(f"\n• <a href=\"{link}\">{title}</a>")
        else:
            lines.append(f"\n• {title}")

        if publisher:
            lines.append(f"  <i>{publisher}</i>")

    return "\n".join(lines)
