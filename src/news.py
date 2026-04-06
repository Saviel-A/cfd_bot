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
        return f"<b>{symbol}</b>\n\nNo recent news found."

    lines = [f"📰 <b>{symbol} — Latest News</b>"]
    for item in news:
        title     = item.get("title") or "No title"
        publisher = item.get("publisher", "")
        summary   = item.get("summary", "")
        link      = item.get("link", "")

        if link:
            lines.append(f"\n• <a href=\"{link}\">{title}</a>")
        else:
            lines.append(f"\n• <b>{title}</b>")

        if summary:
            # Trim long summaries
            short = summary[:120] + "…" if len(summary) > 120 else summary
            lines.append(f"  <i>{short}</i>")

        if publisher:
            lines.append(f"  <i>— {publisher}</i>")

    return "\n".join(lines)
