from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import Message

from bot.config import Config
from bot.database.repository import Repository
from bot.services.llm import LLMService

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.photo)
async def handle_photo(message: Message, llm: LLMService, repo: Repository, config: Config) -> None:
    user = message.from_user
    await repo.upsert_user(user.id, user.username, user.first_name)

    limit_msg = await llm.check_limits(user.id)
    if limit_msg:
        await message.answer(limit_msg)
        return

    typing = await message.answer("👁 Analyzing image…")

    try:
        # get highest resolution photo
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{config.telegram_bot_token}/{file.file_path}"

        caption = message.caption or ""
        response = await llm.chat_vision(user.id, file_url, caption)
    except Exception:
        logger.exception("Vision error")
        await typing.edit_text("Failed to analyze image.")
        return

    if len(response) <= 4096:
        await typing.edit_text(response)
    else:
        await typing.delete()
        for i in range(0, len(response), 4096):
            await message.answer(response[i:i + 4096])
