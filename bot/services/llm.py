from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

from bot.config import Config
from bot.database.repository import Repository

if TYPE_CHECKING:
    from bot.services.gemini import GeminiService

logger = logging.getLogger(__name__)

# minimum interval between message edits (Telegram rate limit)
STREAM_EDIT_INTERVAL = 1.5


class LLMService:
    def __init__(self, config: Config, repo: Repository, gemini: GeminiService | None = None) -> None:
        self._client = AsyncOpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
        )
        self._config = config
        self._repo = repo
        self._gemini = gemini
        self._skills_prompt: str = ""

    def set_skills_prompt(self, prompt: str) -> None:
        """Set skills context to inject into system prompt."""
        self._skills_prompt = prompt
        if self._gemini:
            self._gemini.set_skills_prompt(prompt)

    async def check_limits(self, user_id: int) -> str | None:
        """Return error message if limits exceeded, None otherwise."""
        cfg = self._config
        if cfg.daily_token_limit > 0:
            daily = await self._repo.get_daily_tokens(user_id)
            if daily >= cfg.daily_token_limit:
                return f"Daily token limit reached ({cfg.daily_token_limit:,} tokens). Try again tomorrow."
        if cfg.monthly_token_limit > 0:
            monthly = await self._repo.get_monthly_tokens(user_id)
            if monthly >= cfg.monthly_token_limit:
                return f"Monthly token limit reached ({cfg.monthly_token_limit:,} tokens)."
        return None

    async def _ensure_conversation(self, user_id: int) -> tuple[int, dict]:
        conv = await self._repo.get_active_conversation(user_id)
        if conv is None:
            conv_id = await self._repo.create_conversation(user_id, self._config.default_model)
            conv = await self._repo.get_active_conversation(user_id)
        else:
            conv_id = conv["id"]
        return conv_id, conv

    async def _get_memory_prompt(self, user_id: int) -> str:
        """Build memory context from stored user facts."""
        facts = await self._repo.get_user_facts(user_id, limit=30)
        if not facts:
            return ""
        lines = ["Known facts about this user (use them to personalize your responses):"]
        for f in facts:
            lines.append(f"- {f['fact']}")
        return "\n".join(lines)

    async def _extract_and_save_facts(self, user_id: int, user_message: str, assistant_response: str) -> None:
        """Ask LLM to extract personal facts from the conversation."""
        if self._gemini:
            await self._gemini._extract_and_save_facts(user_id, user_message, assistant_response)
            return
        try:
            response = await self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": (
                        "Extract personal facts about the user from this conversation exchange. "
                        "Only extract FACTUAL info the user explicitly stated about themselves: "
                        "name, age, location, profession, hobbies, preferences, family, pets, etc. "
                        "Do NOT extract opinions, questions, or temporary states. "
                        "Return each fact on a new line, without numbering or bullets. "
                        "If there are no personal facts, respond with exactly: NONE"
                    )},
                    {"role": "user", "content": f"User said: {user_message}\nAssistant replied: {assistant_response[:500]}"},
                ],
                max_tokens=200,
            )
            answer = response.choices[0].message.content.strip()
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
            logger.debug("Failed to extract facts", exc_info=True)

    def _build_messages(self, conv: dict, history: list[dict], memory_prompt: str = "") -> list[dict]:
        system = conv["system_prompt"]
        if self._skills_prompt:
            system += "\n\n" + self._skills_prompt
        if memory_prompt:
            system += "\n\n" + memory_prompt
        messages: list[dict] = [{"role": "system", "content": system}]
        total_chars = len(conv["system_prompt"])
        max_chars = self._config.max_context_tokens * 4

        for msg in reversed(history):
            total_chars += len(msg["content"])
            if total_chars > max_chars:
                break
            # Vision messages from history: include only the text caption.
            # Never re-send the image URL — Telegram URLs expire quickly and
            # cause BadRequestError: invalid_image_url on subsequent calls.
            messages.insert(1, {"role": msg["role"], "content": msg["content"]})

        return messages

    async def _delete_last_user_message(self, conv_id: int) -> None:
        """Delete the last user message (used for Gemini fallback cleanup)."""
        await self._repo.delete_last_user_message(conv_id)

    async def chat(self, user_id: int, user_message: str) -> str:
        if self._gemini:
            try:
                return await self._gemini.chat(user_id, user_message)
            except Exception as e:
                logger.warning("Gemini chat failed (%s), falling back to OpenAI", e)
                # Gemini added user message before failing — remove it
                conv_id, _ = await self._ensure_conversation(user_id)
                await self._delete_last_user_message(conv_id)

        return await self._chat_openai(user_id, user_message)

    async def _chat_openai(self, user_id: int, user_message: str) -> str:
        conv_id, conv = await self._ensure_conversation(user_id)
        await self._repo.add_message(conv_id, "user", user_message)

        memory_prompt = await self._get_memory_prompt(user_id)
        history = await self._repo.get_messages(conv_id, self._config.max_context_messages)
        messages = self._build_messages(conv, history, memory_prompt)
        model = conv["model"]
        logger.info("LLM request: model=%s, messages=%d", model, len(messages))

        response = await self._client.chat.completions.create(model=model, messages=messages)
        assistant_text = response.choices[0].message.content
        tokens_used = response.usage.total_tokens if response.usage else 0

        await self._repo.add_message(conv_id, "assistant", assistant_text, tokens_used)
        await self._repo.log_api_usage(user_id, "chat", model, tokens_used)

        # extract facts in background
        asyncio.create_task(self._extract_and_save_facts(user_id, user_message, assistant_text))
        return assistant_text

    async def chat_stream(self, user_id: int, user_message: str, on_chunk):
        """Stream chat response, calling on_chunk(accumulated_text) periodically."""
        if self._gemini:
            try:
                return await self._gemini.chat_stream(user_id, user_message, on_chunk)
            except Exception as e:
                logger.warning("Gemini chat_stream failed (%s), falling back to OpenAI", e)
                conv_id, _ = await self._ensure_conversation(user_id)
                await self._delete_last_user_message(conv_id)

        return await self._chat_stream_openai(user_id, user_message, on_chunk)

    async def _chat_stream_openai(self, user_id: int, user_message: str, on_chunk):
        conv_id, conv = await self._ensure_conversation(user_id)
        await self._repo.add_message(conv_id, "user", user_message)

        memory_prompt = await self._get_memory_prompt(user_id)
        history = await self._repo.get_messages(conv_id, self._config.max_context_messages)
        messages = self._build_messages(conv, history, memory_prompt)
        model = conv["model"]
        logger.info("LLM stream request: model=%s, messages=%d", model, len(messages))

        stream = await self._client.chat.completions.create(
            model=model, messages=messages, stream=True,
        )

        full_text = ""
        last_edit = 0
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices[0].delta.content else ""
            full_text += delta

            now = asyncio.get_running_loop().time()
            if now - last_edit >= STREAM_EDIT_INTERVAL and full_text:
                await on_chunk(full_text + " ▌")
                last_edit = now

        # final update
        if full_text:
            await on_chunk(full_text)

        # estimate tokens for streamed response
        tokens_est = len(full_text) // 4
        await self._repo.add_message(conv_id, "assistant", full_text, tokens_est)
        await self._repo.log_api_usage(user_id, "chat", model, tokens_est)

        # extract facts in background
        asyncio.create_task(self._extract_and_save_facts(user_id, user_message, full_text))
        return full_text

    async def chat_with_search(self, user_id: int, user_message: str, search_results: str, on_chunk=None) -> str:
        """Chat with injected context (skill results, etc.)."""
        if self._gemini:
            try:
                return await self._gemini.chat_with_search(user_id, user_message, search_results, on_chunk)
            except Exception as e:
                logger.warning("Gemini chat_with_search failed (%s), falling back to OpenAI", e)
                conv_id, _ = await self._ensure_conversation(user_id)
                await self._delete_last_user_message(conv_id)

        return await self._chat_with_search_openai(user_id, user_message, search_results, on_chunk)

    async def _chat_with_search_openai(self, user_id: int, user_message: str, search_results: str, on_chunk=None) -> str:
        conv_id, conv = await self._ensure_conversation(user_id)
        await self._repo.add_message(conv_id, "user", user_message)

        memory_prompt = await self._get_memory_prompt(user_id)
        history = await self._repo.get_messages(conv_id, self._config.max_context_messages)
        messages = self._build_messages(conv, history, memory_prompt)

        # inject context as system message right before the user's question
        search_system = (
            "IMPORTANT: The following data was obtained for the user's query. "
            "You MUST use it to provide an accurate answer. "
            "Cite sources with URLs where appropriate.\n\n"
            f"{search_results}"
        )
        messages.insert(-1, {"role": "system", "content": search_system})

        model = conv["model"]
        logger.info("LLM+context request: model=%s, messages=%d", model, len(messages))

        if on_chunk:
            stream = await self._client.chat.completions.create(
                model=model, messages=messages, stream=True,
            )
            full_text = ""
            last_edit = 0
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices[0].delta.content else ""
                full_text += delta
                now = asyncio.get_running_loop().time()
                if now - last_edit >= STREAM_EDIT_INTERVAL and full_text:
                    await on_chunk(full_text + " ▌")
                    last_edit = now
            if full_text:
                await on_chunk(full_text)
            tokens_est = len(full_text) // 4
            await self._repo.add_message(conv_id, "assistant", full_text, tokens_est)
            await self._repo.log_api_usage(user_id, "chat", model, tokens_est)
            return full_text
        else:
            response = await self._client.chat.completions.create(model=model, messages=messages)
            assistant_text = response.choices[0].message.content
            tokens_used = response.usage.total_tokens if response.usage else 0
            await self._repo.add_message(conv_id, "assistant", assistant_text, tokens_used)
            await self._repo.log_api_usage(user_id, "chat", model, tokens_used)
            return assistant_text

    async def chat_web_search(self, user_id: int, user_message: str, on_chunk=None) -> str:
        """Chat with web search. Tries Gemini (Google Search grounding), falls back to OpenAI."""
        if self._gemini:
            try:
                return await self._gemini.chat_web_search(user_id, user_message, on_chunk)
            except Exception as e:
                logger.warning("Gemini web_search failed (%s), falling back to OpenAI", e)
                conv_id, _ = await self._ensure_conversation(user_id)
                await self._delete_last_user_message(conv_id)

        return await self._chat_web_search_openai(user_id, user_message, on_chunk)

    async def _chat_web_search_openai(self, user_id: int, user_message: str, on_chunk=None) -> str:
        """Chat using OpenAI Responses API with built-in web_search tool."""
        conv_id, conv = await self._ensure_conversation(user_id)
        await self._repo.add_message(conv_id, "user", user_message)

        history = await self._repo.get_messages(conv_id, self._config.max_context_messages)
        model = conv["model"]

        # build input for Responses API
        memory_prompt = await self._get_memory_prompt(user_id)
        system = conv["system_prompt"]
        if self._skills_prompt:
            system += "\n\n" + self._skills_prompt
        if memory_prompt:
            system += "\n\n" + memory_prompt

        input_messages = [{"role": "system", "content": system}]
        total_chars = len(system)
        max_chars = self._config.max_context_tokens * 4

        for msg in reversed(history):
            total_chars += len(msg["content"])
            if total_chars > max_chars:
                break
            input_messages.insert(1, {"role": msg["role"], "content": msg["content"]})

        logger.info("Responses API web_search: model=%s, messages=%d", model, len(input_messages))

        try:
            response = await self._client.responses.create(
                model=model,
                input=input_messages,
                tools=[{"type": "web_search_preview", "search_context_size": "medium"}],
                tool_choice="auto",
            )

            # extract text from response
            assistant_text = response.output_text

            # extract citations
            sources = []
            for item in response.output:
                if hasattr(item, "content"):
                    for block in item.content:
                        if hasattr(block, "annotations"):
                            for ann in block.annotations:
                                if hasattr(ann, "url") and hasattr(ann, "title"):
                                    sources.append(f"[{ann.title}]({ann.url})")

            if sources:
                unique_sources = list(dict.fromkeys(sources))[:5]
                assistant_text += "\n\nИсточники:\n" + "\n".join(f"• {s}" for s in unique_sources)

            tokens_used = response.usage.total_tokens if response.usage else len(assistant_text) // 4
            await self._repo.add_message(conv_id, "assistant", assistant_text, tokens_used)
            await self._repo.log_api_usage(user_id, "web_search", model, tokens_used)

            if on_chunk:
                await on_chunk(assistant_text)

            return assistant_text

        except Exception as e:
            logger.warning("Responses API failed (%s), falling back to DuckDuckGo search", e)
            # fallback: remove the user message we already added
            await self._repo.delete_last_exchange(conv_id)
            raise

    async def should_search(self, user_message: str) -> bool:
        """Ask LLM if web search is needed. Returns True/False."""
        if self._gemini:
            try:
                return await self._gemini.should_search(user_message)
            except Exception as e:
                logger.warning("Gemini should_search failed (%s), falling back to OpenAI", e)

        response = await self._client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "You decide if a web search is needed to answer the user's message. "
                    "If the message asks about current events, real-time data, recent news, "
                    "specific facts you might not know, prices, weather, or anything that "
                    "requires up-to-date information — respond with ONLY the word YES. "
                    "If no search is needed (general chat, coding, math, creative tasks) — respond with ONLY the word NO. "
                    "Never explain, just output YES or NO."
                )},
                {"role": "user", "content": user_message},
            ],
            max_tokens=10,
        )
        answer = response.choices[0].message.content.strip().upper()
        return answer == "YES"

    async def chat_vision(self, user_id: int, image_url: str, caption: str = "") -> str:
        if self._gemini:
            try:
                return await self._gemini.chat_vision(user_id, image_url, caption)
            except Exception as e:
                logger.warning("Gemini vision failed (%s), falling back to OpenAI", e)
                conv_id, _ = await self._ensure_conversation(user_id)
                await self._delete_last_user_message(conv_id)

        return await self._chat_vision_openai(user_id, image_url, caption)

    async def _chat_vision_openai(self, user_id: int, image_url: str, caption: str = "") -> str:
        import base64
        import httpx

        conv_id, conv = await self._ensure_conversation(user_id)
        text = caption or "What do you see in this image?"
        # Store only the caption text — Telegram URLs expire and break future calls
        await self._repo.add_message(conv_id, "user", text, content_type="vision")

        # Download image and send as base64 so OpenAI can access it
        async with httpx.AsyncClient() as http:
            resp = await http.get(image_url, timeout=15)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg")
            if "octet-stream" in content_type:
                url_lower = image_url.lower()
                content_type = "image/png" if ".png" in url_lower else "image/jpeg"
            b64 = base64.b64encode(resp.content).decode()
            data_url = f"data:{content_type};base64,{b64}"

        history = await self._repo.get_messages(conv_id, self._config.max_context_messages)
        messages = self._build_messages(conv, history)
        # Replace the last user message with the image content block
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "user":
                messages[i]["content"] = [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
                break

        model = conv["model"]
        logger.info("Vision request: model=%s", model)

        response = await self._client.chat.completions.create(model=model, messages=messages)
        assistant_text = response.choices[0].message.content
        tokens_used = response.usage.total_tokens if response.usage else 0

        await self._repo.add_message(conv_id, "assistant", assistant_text, tokens_used)
        await self._repo.log_api_usage(user_id, "vision", model, tokens_used)
        return assistant_text

    async def retry_last(self, user_id: int) -> str | None:
        conv_id, conv = await self._ensure_conversation(user_id)
        # get the last user message before deleting
        history = await self._repo.get_messages(conv_id, 2)
        if len(history) < 2:
            return None
        # find last user message
        last_user_msg = None
        for msg in reversed(history):
            if msg["role"] == "user":
                last_user_msg = msg["content"]
                break
        if not last_user_msg:
            return None

        await self._repo.delete_last_exchange(conv_id)

        # re-send (uses Gemini fallback via self.chat)
        return await self.chat(user_id, last_user_msg)

    async def generate_image(self, user_id: int, prompt: str) -> bytes | str:
        """Generate image. Tries Gemini first, falls back to OpenAI."""
        if self._gemini:
            try:
                return await self._gemini.generate_image(user_id, prompt)
            except Exception as e:
                logger.warning("Gemini image gen failed (%s), falling back to OpenAI", e)

        return await self._generate_image_openai(user_id, prompt)

    async def _generate_image_openai(self, user_id: int, prompt: str) -> bytes | str:
        """Generate image via OpenAI. Returns bytes (base64-decoded) or URL string."""
        model = self._config.image_model
        # gpt-image-1 only supports b64_json
        if "gpt-image" in model:
            response = await self._client.images.generate(
                model=model,
                prompt=prompt,
                n=1,
                size="1024x1024",
            )
            await self._repo.log_api_usage(user_id, "image", model)
            return base64.b64decode(response.data[0].b64_json)
        else:
            response = await self._client.images.generate(
                model=model,
                prompt=prompt,
                n=1,
                size="1024x1024",
            )
            await self._repo.log_api_usage(user_id, "image", model)
            return response.data[0].url
