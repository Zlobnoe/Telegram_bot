from __future__ import annotations

import tempfile
import logging

from aiogram import Router, F
from aiogram.types import Message

from bot.config import Config
from bot.database.repository import Repository
from bot.services.llm import LLMService
from bot.services.stt import STTService

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.voice)
async def handle_voice(message: Message, llm: LLMService, stt: STTService, repo: Repository, config: Config) -> None:
    user = message.from_user
    await repo.upsert_user(user.id, user.username, user.first_name)

    typing = await message.answer("🎤 Transcribing…")

    try:
        file = await message.bot.get_file(message.voice.file_id)
        ogg_path = tempfile.mktemp(suffix=".ogg")
        await message.bot.download_file(file.file_path, ogg_path)

        text = await stt.transcribe(ogg_path)
        await repo.log_api_usage(user.id, "stt", config.whisper_model)
        await typing.edit_text(f"🎤 _{text}_\n\n⏳ Thinking…", parse_mode="Markdown")

        response = await llm.chat(user.id, text)
    except Exception:
        logger.exception("Voice handling error")
        await typing.edit_text("Failed to process voice message.")
        return

    if len(response) <= 4096:
        await typing.edit_text(f"🎤 _{text}_\n\n{response}", parse_mode="Markdown")
    else:
        await typing.edit_text(f"🎤 _{text}_", parse_mode="Markdown")
        for i in range(0, len(response), 4096):
            await message.answer(response[i : i + 4096])
