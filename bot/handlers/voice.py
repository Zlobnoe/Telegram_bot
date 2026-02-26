from __future__ import annotations

import html
import os
import tempfile
import logging

from aiogram import Router, F
from aiogram.types import Message

from bot.config import Config
from bot.database.repository import Repository
from bot.services.llm import LLMService
from bot.services.stt import STTService
from bot.utils import md_to_html, safe_reply

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.voice)
async def handle_voice(message: Message, llm: LLMService, stt: STTService, repo: Repository, config: Config) -> None:
    user = message.from_user
    await repo.upsert_user(user.id, user.username, user.first_name)

    typing = await message.answer("🎤 Transcribing…")

    fd, ogg_path = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    try:
        file = await message.bot.get_file(message.voice.file_id)
        await message.bot.download_file(file.file_path, ogg_path)

        text = await stt.transcribe(ogg_path)
        await repo.log_api_usage(user.id, "stt", config.whisper_model)

        safe_text = html.escape(text)
        await typing.edit_text(f"🎤 <i>{safe_text}</i>\n\n⏳ Thinking…", parse_mode="HTML")

        response = await llm.chat(user.id, text)
    except Exception:
        logger.exception("Voice handling error")
        await typing.edit_text("Failed to process voice message.")
        return
    finally:
        os.unlink(ogg_path)

    safe_text = html.escape(text)
    full = f"🎤 <i>{safe_text}</i>\n\n{md_to_html(response)}"

    if len(full) <= 4096:
        try:
            await typing.edit_text(full, parse_mode="HTML")
        except Exception:
            await typing.edit_text(f"🎤 <i>{safe_text}</i>", parse_mode="HTML")
            await safe_reply(message, response)
    else:
        await typing.edit_text(f"🎤 <i>{safe_text}</i>", parse_mode="HTML")
        await safe_reply(message, response)
