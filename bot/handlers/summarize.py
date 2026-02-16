from __future__ import annotations

import logging
import re

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from bot.database.repository import Repository
from bot.services.llm import LLMService
from bot.services.search import fetch_page_text
from bot.handlers.chat import _send_response
from bot.utils import safe_edit

logger = logging.getLogger(__name__)
router = Router()

URL_REGEX = re.compile(r"https?://[^\s<>\"']+")


@router.message(Command("sum"))
async def cmd_summarize_url(message: Message, llm: LLMService, repo: Repository) -> None:
    """Summarize a URL: /sum https://example.com/article"""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /sum <url>")
        return

    url_match = URL_REGEX.search(parts[1])
    if not url_match:
        await message.answer("No valid URL found. Send a link to summarize.")
        return

    url = url_match.group(0)
    user = message.from_user
    await repo.upsert_user(user.id, user.username, user.first_name)

    await _summarize_url(message, url, llm, user.id)


@router.message(F.text & F.text.regexp(URL_REGEX))
async def handle_url_message(message: Message, llm: LLMService, repo: Repository) -> None:
    """Auto-detect URLs in messages and offer to summarize."""
    text = message.text or ""
    # only trigger if the message is basically just a URL (maybe with a short comment)
    urls = URL_REGEX.findall(text)
    if not urls:
        return

    # only auto-summarize if the message is mostly a URL (< 50 chars of non-URL text)
    non_url_text = URL_REGEX.sub("", text).strip()
    if len(non_url_text) > 50:
        return  # too much text, not just a URL drop — let chat handler handle it

    user = message.from_user
    await repo.upsert_user(user.id, user.username, user.first_name)

    await _summarize_url(message, urls[0], llm, user.id)


async def _summarize_url(message: Message, url: str, llm: LLMService, user_id: int) -> None:
    """Fetch URL content and summarize it."""
    typing = await message.answer("📄 Загружаю страницу…")

    try:
        page_text = await fetch_page_text(url, max_chars=8000)
        if not page_text or len(page_text) < 50:
            await typing.edit_text("Не удалось извлечь текст с этой страницы.")
            return

        await typing.edit_text("📝 Создаю краткое изложение…")

        async def on_chunk(text: str):
            try:
                await typing.edit_text(text[:4096])
            except Exception:
                pass

        prompt = f"Пользователь отправил ссылку: {url}\nСделай подробное краткое изложение."
        context = (
            f"Page URL: {url}\n"
            f"Page content ({len(page_text)} chars):\n{page_text}"
        )
        response = await llm.chat_with_search(user_id, prompt, context, on_chunk)
        await _send_response(typing, message, response)

    except Exception:
        logger.exception("URL summarization error")
        await typing.edit_text("Ошибка при обработке ссылки.")
