from __future__ import annotations

import re
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.services.gcal import GCalService
from bot.utils import safe_reply

router = Router()

NOT_CONFIGURED = "Google Calendar не настроен. Добавьте GOOGLE_CREDENTIALS_PATH и GOOGLE_CALENDAR_ID в .env"


def _format_event(ev: dict) -> str:
    start = ev.get("start", {})
    dt_str = start.get("dateTime", start.get("date", ""))
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        time_str = dt.strftime("%H:%M")
    except (ValueError, AttributeError):
        time_str = dt_str

    summary = ev.get("summary", "(без названия)")
    eid = ev.get("id", "")
    short_id = eid[:8] if eid else ""
    return f"  {time_str} — {summary}  <code>{short_id}</code>"


def _format_events(events: list[dict], title: str) -> str:
    if not events:
        return f"{title}\n  Нет событий"
    lines = [title]
    for ev in events:
        lines.append(_format_event(ev))
    return "\n".join(lines)


def _today() -> datetime:
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


@router.message(Command("gcal"))
async def cmd_gcal(message: Message, gcal: GCalService | None = None) -> None:
    if gcal is None:
        await message.answer(NOT_CONFIGURED)
        return

    text = (message.text or "").split(maxsplit=1)
    sub = text[1].strip() if len(text) > 1 else ""

    # /gcal (today)
    if not sub:
        today = _today()
        events = await gcal.get_events(today, today + timedelta(days=1))
        await safe_reply(message, _format_events(events, "📅 <b>Сегодня:</b>"))
        return

    # /gcal tomorrow
    if sub.lower() in ("tomorrow", "завтра"):
        today = _today()
        events = await gcal.get_events(today + timedelta(days=1), today + timedelta(days=2))
        await safe_reply(message, _format_events(events, "📅 <b>Завтра:</b>"))
        return

    # /gcal week
    if sub.lower() in ("week", "неделя", "неделю"):
        today = _today()
        events = await gcal.get_events(today, today + timedelta(days=7))
        await safe_reply(message, _format_events(events, "📅 <b>Неделя:</b>"))
        return

    # /gcal add 2026-02-20 14:00 Встреча
    # /gcal add 2026-02-20 14:00-16:00 Конференция
    if sub.startswith("add "):
        await _handle_add(message, gcal, sub[4:].strip())
        return

    # /gcal del <event_id>
    if sub.startswith("del "):
        await _handle_del(message, gcal, sub[4:].strip())
        return

    await message.answer(
        "Использование:\n"
        "/gcal — события на сегодня\n"
        "/gcal tomorrow — на завтра\n"
        "/gcal week — на неделю\n"
        "/gcal add 2026-02-20 14:00 Встреча\n"
        "/gcal add 2026-02-20 14:00-16:00 Конференция\n"
        "/gcal del &lt;id&gt; — удалить событие",
        parse_mode="HTML",
    )


async def _handle_add(message: Message, gcal: GCalService, args: str) -> None:
    # Pattern: YYYY-MM-DD HH:MM[-HH:MM] summary
    m = re.match(
        r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?:-(\d{2}:\d{2}))?\s+(.+)",
        args,
    )
    if not m:
        await message.answer(
            "Формат: /gcal add 2026-02-20 14:00 Название\n"
            "или: /gcal add 2026-02-20 14:00-16:00 Название"
        )
        return

    date_str, start_time, end_time, summary = m.groups()

    try:
        start = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("Неверный формат даты/времени.")
        return

    if end_time:
        try:
            end = datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            await message.answer("Неверный формат времени окончания.")
            return
    else:
        end = start + timedelta(hours=1)

    event = await gcal.create_event(summary, start, end)
    eid = event.get("id", "")[:8]
    await safe_reply(
        message,
        f"✅ Событие создано!\n"
        f"  {start.strftime('%Y-%m-%d %H:%M')} — {end.strftime('%H:%M')}\n"
        f"  {summary}\n"
        f"  ID: <code>{eid}</code>",
    )


async def _handle_del(message: Message, gcal: GCalService, event_id: str) -> None:
    if not event_id:
        await message.answer("Использование: /gcal del <id>")
        return

    # User passes short id (first 8 chars). We need to find full id.
    # Search today ± 30 days to find matching event
    today = _today()
    events = await gcal.get_events(today - timedelta(days=30), today + timedelta(days=60))

    full_id = None
    for ev in events:
        if ev.get("id", "").startswith(event_id):
            full_id = ev["id"]
            break

    if not full_id:
        await message.answer(f"Событие с ID <code>{event_id}</code> не найдено.", parse_mode="HTML")
        return

    ok = await gcal.delete_event(full_id)
    if ok:
        await message.answer(f"🗑 Событие <code>{event_id}</code> удалено.", parse_mode="HTML")
    else:
        await message.answer("Не удалось удалить событие.")
