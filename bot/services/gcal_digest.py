from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from bot.config import Config
from bot.services.gcal import GCalService

logger = logging.getLogger(__name__)


class GCalDigestService:
    """Sends daily morning digest of today's calendar events."""

    def __init__(self, bot: Bot, config: Config, gcal: GCalService) -> None:
        self._bot = bot
        self._config = config
        self._gcal = gcal
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "GCal daily digest scheduled at %02d:00 (%s)",
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
            logger.info("GCal digest: next run in %.0f seconds", wait)
            await asyncio.sleep(wait)

            try:
                await self._send_digest()
            except Exception:
                logger.exception("GCal digest error")

            # sleep a bit to avoid double-firing
            await asyncio.sleep(60)

    async def _send_digest(self) -> None:
        tz = ZoneInfo(self._config.timezone)
        today = datetime.now(tz).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None,
        )
        tomorrow = today + timedelta(days=1)

        events = await self._gcal.get_events(today, tomorrow)

        if not events:
            text = "📅 <b>Доброе утро!</b>\nНа сегодня событий нет."
        else:
            lines = [f"📅 <b>Доброе утро! Сегодня {today.strftime('%d.%m.%Y')}:</b>"]
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
            text = "\n".join(lines)

        # Send to admin (single user bot)
        chat_id = self._config.admin_id
        if not chat_id:
            logger.warning("GCal digest: ADMIN_ID not set, skipping")
            return

        try:
            await self._bot.send_message(chat_id, text, parse_mode="HTML")
            logger.info("GCal digest sent to %d (%d events)", chat_id, len(events))
        except Exception:
            logger.exception("Failed to send GCal digest")
