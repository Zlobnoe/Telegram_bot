from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import feedparser

if TYPE_CHECKING:
    from bot.database.repository import Repository

logger = logging.getLogger(__name__)

# Maximum items shown per /news call
MAX_ITEMS = 10


def _entry_published_at(entry) -> datetime:
    """Parse publication time from a feedparser entry; fall back to now."""
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


async def fetch_feed(url: str) -> list[dict]:
    """Fetch and parse one RSS feed in a thread pool. Returns list of items."""
    loop = asyncio.get_running_loop()
    try:
        parsed = await loop.run_in_executor(None, feedparser.parse, url)
    except Exception as exc:
        logger.warning("Failed to fetch feed %s: %s", url, exc)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    items = []
    for entry in parsed.entries:
        pub = _entry_published_at(entry)
        if pub < cutoff:
            continue
        link = entry.get("link", "")
        title = entry.get("title", "").strip()
        if not link or not title:
            continue
        items.append({"title": title, "url": link, "published": pub})
    return items


async def get_news_for_user(
    user_id: int,
    repo: "Repository",
    max_items: int = MAX_ITEMS,
) -> list[dict]:
    """
    Fetch news from all user's RSS sources.
    Filters out:
      - Articles already shown in the last 24 h
      - Articles from sources the user has disliked before (soft filter)
    Returns up to max_items articles, newest first.
    """
    sources = await repo.list_news_sources(user_id)
    if not sources:
        return []

    shown_urls = await repo.get_shown_urls_last_24h(user_id)
    disliked_sources = set(await repo.get_disliked_sources(user_id))

    # Fetch all feeds concurrently
    tasks = [fetch_feed(s["url"]) for s in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[dict] = []
    for source, result in zip(sources, results):
        if isinstance(result, BaseException):
            logger.warning("Feed error for %s: %s", source["url"], result)
            continue
        for item in result:
            item["source"] = source["name"] or source["url"]
            all_items.append(item)

    # Sort newest first
    all_items.sort(key=lambda x: x["published"], reverse=True)

    # Filter: skip already seen; deprioritize disliked sources
    preferred, deprioritized = [], []
    for item in all_items:
        if item["url"] in shown_urls:
            continue
        if item["source"] in disliked_sources:
            deprioritized.append(item)
        else:
            preferred.append(item)

    combined = preferred + deprioritized
    return combined[:max_items]
