from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, URLInputFile, BufferedInputFile

from bot.services.llm import LLMService

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("image"))
async def handle_image(message: Message, llm: LLMService) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /image <prompt>")
        return

    prompt = parts[1].strip()
    typing = await message.answer("🎨 Generating image…")

    try:
        result = await llm.generate_image(message.from_user.id, prompt)
        await typing.delete()

        if isinstance(result, bytes):
            # base64-decoded image data (gpt-image-1)
            photo = BufferedInputFile(result, filename="image.png")
            await message.answer_photo(photo, caption=prompt)
        else:
            # URL string (dall-e-3)
            await message.answer_photo(URLInputFile(result), caption=prompt)
    except Exception:
        logger.exception("Image generation error")
        await typing.edit_text("Failed to generate image.")
