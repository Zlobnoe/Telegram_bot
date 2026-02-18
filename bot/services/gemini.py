from __future__ import annotations

import asyncio
import logging
from io import BytesIO

from google import genai
from google.genai import types

from bot.config import Config
from bot.database.repository import Repository

logger = logging.getLogger(__name__)

STREAM_EDIT_INTERVAL = 1.5


class GeminiService:
    def __init__(self, config: Config, repo: Repository) -> None:
        self._client = genai.Client(api_key=config.gemini_api_key)
        self._config = config
        self._repo = repo
        self._skills_prompt: str = ""

    def set_skills_prompt(self, prompt: str) -> None:
        self._skills_prompt = prompt

    # ── helpers ──────────────────────────────────────────────────

    async def _ensure_conversation(self, user_id: int) -> tuple[int, dict]:
        conv = await self._repo.get_active_conversation(user_id)
        if conv is None:
            await self._repo.create_conversation(user_id, self._config.default_model)
            conv = await self._repo.get_active_conversation(user_id)
        return conv["id"], conv

    async def _get_memory_prompt(self, user_id: int) -> str:
        facts = await self._repo.get_user_facts(user_id, limit=30)
        if not facts:
            return ""
        lines = ["Known facts about this user (use them to personalize your responses):"]
        for f in facts:
            lines.append(f"- {f['fact']}")
        return "\n".join(lines)

    def _build_system_instruction(self, conv: dict, memory_prompt: str = "") -> str:
        system = conv["system_prompt"]
        if self._skills_prompt:
            system += "\n\n" + self._skills_prompt
        if memory_prompt:
            system += "\n\n" + memory_prompt
        return system

    def _build_contents(self, history: list[dict], max_chars: int) -> list[types.Content]:
        """Convert DB history to Gemini contents format."""
        contents: list[types.Content] = []
        total_chars = 0

        for msg in reversed(history):
            total_chars += len(msg["content"])
            if total_chars > max_chars:
                break
            role = "model" if msg["role"] == "assistant" else "user"
            contents.insert(0, types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])],
            ))
        return contents

    async def _extract_and_save_facts(self, user_id: int, user_message: str, assistant_response: str) -> None:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._config.gemini_model,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "Extract personal facts about the user from this conversation exchange. "
                        "Only extract FACTUAL info the user explicitly stated about themselves: "
                        "name, age, location, profession, hobbies, preferences, family, pets, etc. "
                        "Do NOT extract opinions, questions, or temporary states. "
                        "Return each fact on a new line, without numbering or bullets. "
                        "If there are no personal facts, respond with exactly: NONE"
                    ),
                    max_output_tokens=200,
                ),
            )
            answer = response.text.strip()
            if answer.upper() == "NONE" or len(answer) < 3:
                return

            existing = await self._repo.get_user_facts(user_id, limit=50)
            existing_texts = {f["fact"].lower() for f in existing}

            for line in answer.splitlines():
                fact = line.strip().lstrip("-•").strip()
                if fact and len(fact) > 3 and fact.lower() not in existing_texts:
                    await self._repo.add_user_fact(user_id, fact)
                    logger.info("Saved fact for user %d: %s", user_id, fact)
        except Exception:
            logger.debug("Gemini fact extraction failed", exc_info=True)

    # ── chat ─────────────────────────────────────────────────────

    async def chat(self, user_id: int, user_message: str) -> str:
        conv_id, conv = await self._ensure_conversation(user_id)
        await self._repo.add_message(conv_id, "user", user_message)

        memory_prompt = await self._get_memory_prompt(user_id)
        history = await self._repo.get_messages(conv_id, self._config.max_context_messages)
        max_chars = self._config.max_context_tokens * 4
        contents = self._build_contents(history, max_chars)
        system = self._build_system_instruction(conv, memory_prompt)

        logger.info("Gemini chat request: model=%s, messages=%d", self._config.gemini_model, len(contents))

        response = await self._client.aio.models.generate_content(
            model=self._config.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
            ),
        )

        assistant_text = response.text
        tokens_used = (
            response.usage_metadata.total_token_count
            if response.usage_metadata else 0
        )

        await self._repo.add_message(conv_id, "assistant", assistant_text, tokens_used)
        await self._repo.log_api_usage(user_id, "chat", self._config.gemini_model, tokens_used)

        asyncio.create_task(self._extract_and_save_facts(user_id, user_message, assistant_text))
        return assistant_text

    # ── chat stream ──────────────────────────────────────────────

    async def chat_stream(self, user_id: int, user_message: str, on_chunk) -> str:
        conv_id, conv = await self._ensure_conversation(user_id)
        await self._repo.add_message(conv_id, "user", user_message)

        memory_prompt = await self._get_memory_prompt(user_id)
        history = await self._repo.get_messages(conv_id, self._config.max_context_messages)
        max_chars = self._config.max_context_tokens * 4
        contents = self._build_contents(history, max_chars)
        system = self._build_system_instruction(conv, memory_prompt)

        logger.info("Gemini stream request: model=%s, messages=%d", self._config.gemini_model, len(contents))

        full_text = ""
        last_edit = 0

        async for chunk in self._client.aio.models.generate_content_stream(
            model=self._config.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
            ),
        ):
            delta = chunk.text or ""
            full_text += delta

            now = asyncio.get_event_loop().time()
            if now - last_edit >= STREAM_EDIT_INTERVAL and full_text:
                await on_chunk(full_text + " ▌")
                last_edit = now

        if full_text:
            await on_chunk(full_text)

        tokens_est = len(full_text) // 4
        await self._repo.add_message(conv_id, "assistant", full_text, tokens_est)
        await self._repo.log_api_usage(user_id, "chat", self._config.gemini_model, tokens_est)

        asyncio.create_task(self._extract_and_save_facts(user_id, user_message, full_text))
        return full_text

    # ── chat with injected search context ────────────────────────

    async def chat_with_search(self, user_id: int, user_message: str, search_results: str, on_chunk=None) -> str:
        conv_id, conv = await self._ensure_conversation(user_id)
        await self._repo.add_message(conv_id, "user", user_message)

        memory_prompt = await self._get_memory_prompt(user_id)
        history = await self._repo.get_messages(conv_id, self._config.max_context_messages)
        max_chars = self._config.max_context_tokens * 4
        contents = self._build_contents(history, max_chars)

        system = self._build_system_instruction(conv, memory_prompt)
        system += (
            "\n\nIMPORTANT: The following data was obtained for the user's query. "
            "You MUST use it to provide an accurate answer. "
            "Cite sources with URLs where appropriate.\n\n"
            f"{search_results}"
        )

        logger.info("Gemini+context request: model=%s, messages=%d", self._config.gemini_model, len(contents))

        if on_chunk:
            full_text = ""
            last_edit = 0
            async for chunk in self._client.aio.models.generate_content_stream(
                model=self._config.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=system),
            ):
                delta = chunk.text or ""
                full_text += delta
                now = asyncio.get_event_loop().time()
                if now - last_edit >= STREAM_EDIT_INTERVAL and full_text:
                    await on_chunk(full_text + " ▌")
                    last_edit = now
            if full_text:
                await on_chunk(full_text)
            tokens_est = len(full_text) // 4
            await self._repo.add_message(conv_id, "assistant", full_text, tokens_est)
            await self._repo.log_api_usage(user_id, "chat", self._config.gemini_model, tokens_est)
            return full_text
        else:
            response = await self._client.aio.models.generate_content(
                model=self._config.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=system),
            )
            assistant_text = response.text
            tokens_used = (
                response.usage_metadata.total_token_count
                if response.usage_metadata else 0
            )
            await self._repo.add_message(conv_id, "assistant", assistant_text, tokens_used)
            await self._repo.log_api_usage(user_id, "chat", self._config.gemini_model, tokens_used)
            return assistant_text

    # ── web search (Google Search grounding) ─────────────────────

    async def chat_web_search(self, user_id: int, user_message: str, on_chunk=None) -> str:
        conv_id, conv = await self._ensure_conversation(user_id)
        await self._repo.add_message(conv_id, "user", user_message)

        memory_prompt = await self._get_memory_prompt(user_id)
        history = await self._repo.get_messages(conv_id, self._config.max_context_messages)
        max_chars = self._config.max_context_tokens * 4
        contents = self._build_contents(history, max_chars)
        system = self._build_system_instruction(conv, memory_prompt)

        logger.info("Gemini web_search: model=%s, messages=%d", self._config.gemini_model, len(contents))

        response = await self._client.aio.models.generate_content(
            model=self._config.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )

        assistant_text = response.text

        # extract citations from grounding metadata
        sources = []
        if response.candidates:
            candidate = response.candidates[0]
            gm = candidate.grounding_metadata
            if gm and gm.grounding_chunks:
                for gc in gm.grounding_chunks:
                    if gc.web and gc.web.uri:
                        title = gc.web.title or gc.web.uri
                        sources.append(f"[{title}]({gc.web.uri})")

        if sources:
            unique_sources = list(dict.fromkeys(sources))[:5]
            assistant_text += "\n\nИсточники:\n" + "\n".join(f"• {s}" for s in unique_sources)

        tokens_used = (
            response.usage_metadata.total_token_count
            if response.usage_metadata else len(assistant_text) // 4
        )
        await self._repo.add_message(conv_id, "assistant", assistant_text, tokens_used)
        await self._repo.log_api_usage(user_id, "web_search", self._config.gemini_model, tokens_used)

        if on_chunk:
            await on_chunk(assistant_text)

        return assistant_text

    # ── should_search ────────────────────────────────────────────

    async def should_search(self, user_message: str) -> bool:
        response = await self._client.aio.models.generate_content(
            model=self._config.gemini_model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You decide if a web search is needed to answer the user's message. "
                    "If the message asks about current events, real-time data, recent news, "
                    "specific facts you might not know, prices, weather, or anything that "
                    "requires up-to-date information — respond with ONLY the word YES. "
                    "If no search is needed (general chat, coding, math, creative tasks) — respond with ONLY the word NO. "
                    "Never explain, just output YES or NO."
                ),
                max_output_tokens=10,
            ),
        )
        answer = response.text.strip().upper()
        return answer == "YES"

    # ── vision ───────────────────────────────────────────────────

    async def chat_vision(self, user_id: int, image_url: str, caption: str = "") -> str:
        conv_id, conv = await self._ensure_conversation(user_id)
        text = caption or "What do you see in this image?"
        await self._repo.add_message(conv_id, "user", text, content_type="vision", image_url=image_url)

        # download the image
        import httpx
        async with httpx.AsyncClient() as http:
            img_resp = await http.get(image_url)
            img_resp.raise_for_status()
            image_bytes = img_resp.content
            content_type = img_resp.headers.get("content-type", "image/jpeg")

        system = self._build_system_instruction(conv)

        parts = [
            types.Part.from_bytes(data=image_bytes, mime_type=content_type),
        ]
        if text:
            parts.append(types.Part.from_text(text=text))

        logger.info("Gemini vision request: model=%s", self._config.gemini_model)

        response = await self._client.aio.models.generate_content(
            model=self._config.gemini_model,
            contents=types.Content(role="user", parts=parts),
            config=types.GenerateContentConfig(system_instruction=system),
        )

        assistant_text = response.text
        tokens_used = (
            response.usage_metadata.total_token_count
            if response.usage_metadata else 0
        )

        await self._repo.add_message(conv_id, "assistant", assistant_text, tokens_used)
        await self._repo.log_api_usage(user_id, "vision", self._config.gemini_model, tokens_used)
        return assistant_text

    # ── image generation ─────────────────────────────────────────

    async def generate_image(self, user_id: int, prompt: str) -> bytes:
        """Generate image using Gemini imagen model. Returns PNG bytes."""
        logger.info("Gemini image generation: prompt=%s", prompt[:80])

        response = await self._client.aio.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        # find inline image data in response
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                await self._repo.log_api_usage(user_id, "image", "gemini-2.0-flash-exp")
                return part.inline_data.data

        raise RuntimeError("Gemini did not return image data")
