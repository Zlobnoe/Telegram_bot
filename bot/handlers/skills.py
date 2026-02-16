from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from bot.services.skills import SkillsService
from bot.services.llm import LLMService
from bot.database.repository import Repository
from bot.handlers.chat import RETRY_KB, _send_response

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("skills"))
async def cmd_skills(message: Message, skill_service: SkillsService) -> None:
    """List all installed skills."""
    await message.answer(skill_service.list_skills_text())


@router.message(F.text.startswith("/"))
async def handle_skill_command(
    message: Message, skill_service: SkillsService, llm: LLMService, repo: Repository
) -> None:
    """Try to match unknown /commands to skill triggers."""
    text = message.text or ""
    command = text.split()[0].lower()
    if "@" in command:
        command = command.split("@")[0]

    skill = skill_service.find_by_trigger(command)
    if not skill:
        return

    user = message.from_user
    await repo.upsert_user(user.id, user.username, user.first_name)

    full_query = text

    typing = await message.answer(f"⚡ {skill.name}…")

    try:
        if skill.execute:
            result = await skill_service.execute_skill(skill, full_query)
            if result:
                async def on_chunk(t: str):
                    try:
                        await typing.edit_text(t[:4096])
                    except Exception:
                        pass

                response = await llm.chat_with_search(
                    user.id, full_query,
                    f"Skill '{skill.name}' executed and returned:\n{result}",
                    on_chunk,
                )
                await _send_response(typing, message, response)
            else:
                await typing.edit_text("Skill returned no result.")
        else:
            async def on_chunk(t: str):
                try:
                    await typing.edit_text(t[:4096])
                except Exception:
                    pass

            response = await llm.chat_stream(user.id, full_query, on_chunk)
            await _send_response(typing, message, response)
    except Exception:
        logger.exception("Skill command error: %s", command)
        await typing.edit_text("Skill execution failed.")
