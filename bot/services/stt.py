from __future__ import annotations

import logging
from pathlib import Path

from openai import AsyncOpenAI

from bot.config import Config

logger = logging.getLogger(__name__)


class STTService:
    def __init__(self, config: Config) -> None:
        self._client = AsyncOpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
        )
        self._model = config.whisper_model

    async def transcribe(self, ogg_path: str) -> str:
        # Whisper API accepts ogg/opus directly — no ffmpeg conversion needed
        with open(ogg_path, "rb") as f:
            transcript = await self._client.audio.transcriptions.create(
                model=self._model,
                file=f,
            )

        return transcript.text
