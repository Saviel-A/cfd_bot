"""News fetcher — uses yfinance to pull recent headlines for a symbol."""

import logging
import yfinance as yf
from src.instruments import get_ticker_for_symbol

logger = logging.getLogger(__name__)


def _parse_item(raw: dict) -> dict:
    """Normalize yfinance news item regardless of API version."""
    # New API: data is nested under 'content'
    content = raw.get("content", {})
    if content:
        url = (
            (content.get("canonicalUrl") or {}).get("url")
            or (content.get("clickThroughUrl") or {}).get("url")
            or ""
        )
        return {
            "title":     content.get("title", ""),
            "publisher": (content.get("provider") or {}).get("displayName", ""),
            "summary":   content.get("summary", ""),
            "link":      url,
        }
    # Legacy API: flat structure
    return {
        "title":     raw.get("title", ""),
        "publisher": raw.get("publisher", ""),
        "summary":   raw.get("summary", ""),
        "link":      raw.get("link") or raw.get("url", ""),
    }


def get_news(symbol: str, limit: int = 5) -> list[dict]:
    """Return up to `limit` recent news items for a symbol."""
    try:
        ticker = get_ticker_for_symbol(symbol)
        t = yf.Ticker(ticker)
        raw = t.news or []
        return [_parse_item(item) for item in raw[:limit]]
    except Exception as e:
        logger.error(f"Failed to fetch news for {symbol}: {e}")
        return []


def format_news_message(symbol: str, news: list[dict]) -> str:
    if not news:
        return f"📰 <b>{symbol} News</b>\n\nNo recent headlines."

    lines = [f"📰 <b>{symbol} News</b>"]
    for i, item in enumerate(news, 1):
        title     = item.get("title") or "No title"
        publisher = item.get("publisher", "")
        link      = item.get("link", "")
        pub_str   = f" ({publisher})" if publisher else ""

        if link:
            lines.append(f"{i}. <a href=\"{link}\">{title}</a>{pub_str}")
        else:
            lines.append(f"{i}. <b>{title}</b>{pub_str}")

        if i < len(news):
            lines.append("")

    return "\n".join(lines)
