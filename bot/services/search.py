from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=3)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def _search_sync(query: str, max_results: int = 5) -> list[dict]:
    from duckduckgo_search import DDGS
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })
    return results


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web using DuckDuckGo."""
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(_executor, _search_sync, query, max_results)
        logger.info("Web search: query=%r, results=%d", query, len(results))
        return results
    except Exception:
        logger.exception("Web search error: %s", query)
        return []


async def fetch_page_text(url: str, max_chars: int = 3000) -> str:
    """Fetch a page and extract readable text."""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # remove noise
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]):
            tag.decompose()

        # try to find main content
        main = soup.find("main") or soup.find("article") or soup.find("div", {"role": "main"})
        if main:
            text = main.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        # clean up whitespace
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)

        return text[:max_chars]
    except Exception:
        logger.debug("Failed to fetch %s", url)
        return ""


async def search_and_fetch(query: str, max_results: int = 5, pages_to_fetch: int = 3) -> str:
    """Search the web AND fetch content from top pages."""
    results = await web_search(query, max_results)
    if not results:
        return "No search results found."

    # fetch top pages in parallel
    urls = [r["url"] for r in results[:pages_to_fetch]]
    page_tasks = [fetch_page_text(url) for url in urls]
    page_contents = await asyncio.gather(*page_tasks)

    # build rich context
    lines = []
    for i, r in enumerate(results):
        lines.append(f"### {i+1}. {r['title']}")
        lines.append(f"URL: {r['url']}")
        lines.append(f"Snippet: {r['snippet']}")

        # add page content if we fetched it
        if i < len(page_contents) and page_contents[i]:
            lines.append(f"Page content:\n{page_contents[i]}")

        lines.append("")

    return "\n".join(lines)


def format_search_results(results: list[dict]) -> str:
    """Format search results as text (snippets only, legacy)."""
    if not results:
        return "No search results found."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**\n   {r['snippet']}\n   {r['url']}")
    return "\n\n".join(lines)
