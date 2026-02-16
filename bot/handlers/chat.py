from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import Config
from bot.database.repository import Repository
from bot.services.llm import LLMService
from bot.services.skills import SkillsService
from bot.utils import safe_edit, safe_reply

logger = logging.getLogger(__name__)
router = Router()

RETRY_KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🔄 Retry", callback_data="retry"),
        InlineKeyboardButton(text="🔊 Voice", callback_data="tts_last"),
    ]
])


async def _send_response(typing: Message, message: Message, response: str) -> None:
    """Send response with formatting, splitting into chunks if needed."""
    if len(response) <= 4096:
        await safe_edit(typing, response, reply_markup=RETRY_KB)
    else:
        await typing.delete()
        chunks = [response[i:i + 4096] for i in range(0, len(response), 4096)]
        for i, chunk in enumerate(chunks):
            kb = RETRY_KB if i == len(chunks) - 1 else None
            await safe_reply(message, chunk, reply_markup=kb)


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, llm: LLMService, repo: Repository, config: Config, skill_service: SkillsService) -> None:
    user = message.from_user
    await repo.upsert_user(user.id, user.username, user.first_name)

    limit_msg = await llm.check_limits(user.id)
    if limit_msg:
        await message.answer(limit_msg)
        return

    typing = await message.answer("⏳")

    async def on_chunk(text: str):
        try:
            await typing.edit_text(text[:4096])
        except Exception:
            pass

    try:
        # check if a skill matches by keywords
        skill = skill_service.find_by_keywords(message.text)
        if skill and skill.execute:
            await typing.edit_text(f"⚡ Running skill: {skill.name}…")
            skill_result = await skill_service.execute_skill(skill, message.text)
            if skill_result:
                response = await llm.chat_with_search(
                    user.id, message.text, f"Skill '{skill.name}' result:\n{skill_result}", on_chunk
                )
                await _send_response(typing, message, response)
                return

        # check if web search is needed
        needs_search = await llm.should_search(message.text)

        if needs_search:
            await typing.edit_text("🔍 Searching the web…")
            response = await llm.chat_web_search(user.id, message.text, on_chunk)
        else:
            response = await llm.chat_stream(user.id, message.text, on_chunk)
    except Exception:
        logger.exception("LLM error")
        try:
            await typing.edit_text("Something went wrong. Please try again.")
        except Exception:
            pass
        return

    await _send_response(typing, message, response)
