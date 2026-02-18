from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import Config
from bot.database.repository import Repository
from bot.services.llm import LLMService
from bot.handlers.chat import RETRY_KB, _send_response

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("search"))
async def cmd_search(message: Message, llm: LLMService, repo: Repository, config: Config) -> None:
    logger.info("/search handler called, text=%s", message.text[:80] if message.text else None)
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /search <query>")
        return

    query = parts[1].strip()
    user = message.from_user
    await repo.upsert_user(user.id, user.username, user.first_name)

    limit_msg = await llm.check_limits(user.id)
    if limit_msg:
        await message.answer(limit_msg)
        return

    typing = await message.answer("🔍 Searching…")

    try:
        async def on_chunk(text: str):
            try:
                await typing.edit_text(text[:4096])
            except Exception:
                pass

        response = await llm.chat_web_search(user.id, query, on_chunk)
    except Exception:
        logger.exception("Search+LLM error")
        await typing.edit_text("Search failed. Please try again.")
        return

    await _send_response(typing, message, response)
