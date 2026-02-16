from __future__ import annotations

import tempfile
from pathlib import Path

from openai import AsyncOpenAI

from bot.config import Config


class TTSService:
    def __init__(self, config: Config) -> None:
        self._client = AsyncOpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
        )

    async def synthesize(self, text: str) -> str:
        response = await self._client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text,
        )
        tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        tmp.write(response.content)
        tmp.close()
        return tmp.name
