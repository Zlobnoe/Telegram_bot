from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from bot.config import Config
from bot.services.gcal import GCalService
from bot.services.weather import WeatherService

logger = logging.getLogger(__name__)


class GCalDigestService:
    """Sends daily morning digest: calendar events + weather forecast."""

    def __init__(
        self,
        bot: Bot,
        config: Config,
        gcal: GCalService | None,
        weather: WeatherService,
    ) -> None:
        self._bot = bot
        self._config = config
        self._gcal = gcal
        self._weather = weather
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Daily digest scheduled at %02d:00 (%s)",
            self._config.gcal_daily_hour, self._config.timezone,
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _seconds_until_next_run(self) -> float:
        tz = ZoneInfo(self._config.timezone)
        now = datetime.now(tz)
        target = now.replace(
            hour=self._config.gcal_daily_hour, minute=0, second=0, microsecond=0,
        )
        if now >= target:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def _loop(self) -> None:
        while True:
            wait = self._seconds_until_next_run()
            logger.info("Daily digest: next run in %.0f seconds", wait)
            await asyncio.sleep(wait)

            try:
                await self._send_digest()
            except Exception:
                logger.exception("Daily digest error")

            # sleep a bit to avoid double-firing
            await asyncio.sleep(60)

    async def _build_calendar_block(self, today: datetime) -> str:
        """Return calendar events block, or empty string if gcal not configured."""
        if self._gcal is None:
            return ""

        tomorrow = today + timedelta(days=1)
        try:
            events = await self._gcal.get_events(today, tomorrow)
        except Exception:
            logger.exception("Failed to fetch calendar events for digest")
            return "📅 Не удалось получить события календаря"

        if not events:
            return f"📅 <b>Сегодня {today.strftime('%d.%m.%Y')}:</b>\nСобытий нет"

        lines = [f"📅 <b>Сегодня {today.strftime('%d.%m.%Y')}:</b>"]
        for ev in events:
            start = ev.get("start", {})
            dt_str = start.get("dateTime", start.get("date", ""))
            try:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M")
            except (ValueError, AttributeError):
                time_str = dt_str
            summary = ev.get("summary", "(без названия)")
            lines.append(f"  {time_str} — {summary}")
        return "\n".join(lines)

    async def _send_digest(self) -> None:
        chat_id = self._config.admin_id
        if not chat_id:
            logger.warning("Daily digest: ADMIN_ID not set, skipping")
            return

        tz = ZoneInfo(self._config.timezone)
        today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

        # Fetch calendar and weather in parallel; errors in either block must not
        # prevent the other from being sent.
        results = await asyncio.gather(
            self._build_calendar_block(today),
            self._weather.get_forecast_text(),
            return_exceptions=True,
        )

        calendar_block = results[0] if not isinstance(results[0], BaseException) else ""
        weather_block = results[1] if not isinstance(results[1], BaseException) else "🌡 Погода временно недоступна"

        if isinstance(results[0], BaseException):
            logger.exception("Calendar block failed: %s", results[0])
        if isinstance(results[1], BaseException):
            logger.exception("Weather block failed: %s", results[1])

        parts = ["☀️ <b>Доброе утро!</b>"]
        if calendar_block:
            parts.append(calendar_block)
        parts.append(weather_block)

        text = "\n\n".join(parts)

        try:
            await self._bot.send_message(chat_id, text, parse_mode="HTML")
            logger.info("Daily digest sent to %d", chat_id)
        except Exception:
            logger.exception("Failed to send daily digest")
