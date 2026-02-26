from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from bot.config import Config
from bot.database.repository import Repository
from bot.services.gcal import GCalService
from bot.services.weather import WeatherService

logger = logging.getLogger(__name__)


def _fmt(val: float) -> str:
    """Format number with thousands separator."""
    if val == int(val):
        return f"{int(val):,}".replace(",", " ")
    return f"{val:,.2f}".replace(",", " ")


class GCalDigestService:
    """Sends daily morning digest: calendar events + weather forecast + news."""

    def __init__(
        self,
        bot: Bot,
        config: Config,
        gcal: GCalService | None,
        weather: WeatherService,
        repo: Repository | None = None,
    ) -> None:
        self._bot = bot
        self._config = config
        self._gcal = gcal
        self._weather = weather
        self._repo = repo
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

    async def _build_news_block(self, user_id: int) -> str:
        """Return top news headlines block, or empty string if no sources."""
        if self._repo is None:
            return ""
        from bot.services.news import get_news_for_user
        try:
            articles = await get_news_for_user(user_id, self._repo, max_items=5)
        except Exception:
            logger.exception("Failed to fetch news for digest")
            return "📰 Не удалось загрузить новости"

        if not articles:
            return ""

        lines = ["📰 <b>Новости:</b>"]
        for a in articles:
            title = html.escape(a["title"])
            url = a["url"]
            lines.append(f'• <a href="{url}">{title}</a>')
        return "\n".join(lines)

    async def _build_expense_block(self, user_id: int, today: datetime) -> str:
        """Return weekly expense summary block; only on Mondays and when data is available."""
        if today.weekday() != 0:  # 0 = Monday
            return ""
        if self._repo is None:
            return ""

        try:
            settings = await self._repo.get_finance_settings(user_id)
        except Exception:
            logger.exception("Failed to fetch finance settings for digest")
            return ""

        if settings is None:
            return ""

        current_week = settings["current_week"]
        current_year = settings["current_year"]
        budget = settings["weekly_budget"]
        if budget <= 0:
            return ""

        # Previous week (with year rollover)
        if current_week > 1:
            prev_week, prev_year = current_week - 1, current_year
        else:
            prev_week, prev_year = 52, current_year - 1

        # Week before that for delta
        if prev_week > 1:
            pprev_week, pprev_year = prev_week - 1, prev_year
        else:
            pprev_week, pprev_year = 52, prev_year - 1

        try:
            prev_records = await self._repo.get_week_expenses(user_id, prev_week, prev_year)
            pprev_records = await self._repo.get_week_expenses(user_id, pprev_week, pprev_year)
        except Exception:
            logger.exception("Failed to fetch expenses for digest")
            return ""

        if not prev_records:
            return ""

        prev_total = sum(r["amount"] for r in prev_records)
        pprev_total = sum(r["amount"] for r in pprev_records)

        pct = (prev_total / budget) * 100
        icon = "🔴" if prev_total > budget else "🟢"

        lines = [f"💰 <b>Итог недели {prev_week}:</b>"]
        lines.append(f"{icon} {_fmt(prev_total)} / {_fmt(budget)} руб. ({pct:.0f}%)")

        if pprev_records:
            delta = prev_total - pprev_total
            sign = "+" if delta > 0 else ""
            lines.append(f"Δ vs неделя {pprev_week}: {sign}{_fmt(delta)} руб.")

        return "\n".join(lines)

    async def _send_digest(self) -> None:
        chat_id = self._config.admin_id
        if not chat_id:
            logger.warning("Daily digest: ADMIN_ID not set, skipping")
            return

        tz = ZoneInfo(self._config.timezone)
        today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

        # Fetch calendar, weather, news, and expenses in parallel
        results = await asyncio.gather(
            self._build_calendar_block(today),
            self._weather.get_forecast_text(),
            self._build_news_block(chat_id),
            self._build_expense_block(chat_id, today),
            return_exceptions=True,
        )

        calendar_block = results[0] if not isinstance(results[0], BaseException) else ""
        weather_block = results[1] if not isinstance(results[1], BaseException) else "🌡 Погода временно недоступна"
        news_block = results[2] if not isinstance(results[2], BaseException) else ""
        expense_block = results[3] if not isinstance(results[3], BaseException) else ""

        if isinstance(results[0], BaseException):
            logger.exception("Calendar block failed: %s", results[0])
        if isinstance(results[1], BaseException):
            logger.exception("Weather block failed: %s", results[1])
        if isinstance(results[2], BaseException):
            logger.exception("News block failed: %s", results[2])
        if isinstance(results[3], BaseException):
            logger.exception("Expense block failed: %s", results[3])

        parts = ["☀️ <b>Доброе утро!</b>"]
        if calendar_block:
            parts.append(calendar_block)
        parts.append(weather_block)
        if news_block:
            parts.append(news_block)
        if expense_block:
            parts.append(expense_block)

        text = "\n\n".join(parts)

        try:
            await self._bot.send_message(chat_id, text, parse_mode="HTML")
            logger.info("Daily digest sent to %d", chat_id)
        except Exception:
            logger.exception("Failed to send daily digest")
