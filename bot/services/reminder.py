from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot

from bot.database.repository import Repository

logger = logging.getLogger(__name__)


class ReminderService:
    """Background scheduler that checks and sends due reminders."""

    def __init__(self, bot: Bot, repo: Repository) -> None:
        self._bot = bot
        self._repo = repo
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("Reminder scheduler started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                reminders = await self._repo.get_pending_reminders()
                for r in reminders:
                    try:
                        await self._bot.send_message(
                            r["chat_id"],
                            f"🔔 Напоминание:\n{r['text']}",
                        )
                        await self._repo.mark_reminder_sent(r["id"])
                        logger.info("Sent reminder #%d to user %d", r["id"], r["user_id"])
                    except Exception:
                        logger.exception("Failed to send reminder #%d", r["id"])
            except Exception:
                logger.exception("Reminder loop error")

            await asyncio.sleep(15)  # check every 15 seconds


def parse_remind_time(text: str) -> tuple[datetime, str] | None:
    """Parse natural language reminder time.

    Supports:
    - 'через 5 минут купить молоко'
    - 'через 2 часа позвонить'
    - 'через 1 день проверить'
    - 'in 30 minutes do something'
    - 'in 2 hours check email'
    """
    import re

    text = text.strip()
    now = datetime.utcnow()

    # Russian: "через X минут/часов/дней ..."
    m = re.match(
        r"(?:через\s+)?(\d+)\s*(мин(?:ут[аыу]?)?|час(?:а|ов)?|дн(?:ей|я)?|день|сек(?:унд[аыу]?)?)\s+(.+)",
        text, re.IGNORECASE,
    )
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        reminder_text = m.group(3)

        if unit.startswith("сек"):
            delta = timedelta(seconds=amount)
        elif unit.startswith("мин"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("час"):
            delta = timedelta(hours=amount)
        elif unit.startswith("дн") or unit == "день":
            delta = timedelta(days=amount)
        else:
            return None

        return now + delta, reminder_text

    # English: "in X minutes/hours/days ..."
    m = re.match(
        r"(?:in\s+)?(\d+)\s*(sec(?:ond)?s?|min(?:ute)?s?|hours?|days?)\s+(.+)",
        text, re.IGNORECASE,
    )
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        reminder_text = m.group(3)

        if unit.startswith("sec"):
            delta = timedelta(seconds=amount)
        elif unit.startswith("min"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("hour"):
            delta = timedelta(hours=amount)
        elif unit.startswith("day"):
            delta = timedelta(days=amount)
        else:
            return None

        return now + delta, reminder_text

    return None
