from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from openai import AsyncOpenAI

from bot.config import Config
from bot.services.gcal import GCalService
from bot.utils import safe_reply

logger = logging.getLogger(__name__)
router = Router()

NOT_CONFIGURED = "Google Calendar не настроен. Добавьте GOOGLE_CREDENTIALS_PATH и GOOGLE_CALENDAR_ID в .env"

PARSE_PROMPT = """\
You are a calendar command parser. Current date/time: {now}.

Parse the user's message into a JSON object with ONE of these actions:
- {{"action": "view", "period": "today"}}
- {{"action": "view", "period": "tomorrow"}}
- {{"action": "view", "period": "week"}}
- {{"action": "add", "date": "YYYY-MM-DD", "start": "HH:MM", "end": "HH:MM", "summary": "text"}}
- {{"action": "delete", "event_id": "id"}}
- {{"action": "unknown"}}

Rules:
- "end" is optional, default to 1 hour after start
- Use 24-hour time format
- If user says "завтра"/"tomorrow", calculate the actual date
- If user says "вечер" (evening) without exact time, use 18:00
- If user says "утро" (morning), use 09:00
- If user says "день"/"обед" (afternoon/lunch), use 13:00
- Reply ONLY with the JSON object, nothing else\
"""


async def _parse_natural(text: str, config: Config) -> dict | None:
    """Use LLM to parse natural language into a structured gcal command."""
    client = AsyncOpenAI(api_key=config.openai_api_key, base_url=config.openai_base_url)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M (UTC)")
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": PARSE_PROMPT.format(now=now)},
                {"role": "user", "content": text},
            ],
            temperature=0,
            max_tokens=200,
        )
        raw = resp.choices[0].message.content.strip()
        # strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)
    except Exception:
        logger.exception("Failed to parse gcal natural language")
        return None


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
async def cmd_gcal(
    message: Message, config: Config, gcal: GCalService | None = None,
) -> None:
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

    # Try exact subcommands first
    sub_lower = sub.lower()

    if sub_lower in ("tomorrow", "завтра"):
        today = _today()
        events = await gcal.get_events(today + timedelta(days=1), today + timedelta(days=2))
        await safe_reply(message, _format_events(events, "📅 <b>Завтра:</b>"))
        return

    if sub_lower in ("week", "неделя", "неделю"):
        today = _today()
        events = await gcal.get_events(today, today + timedelta(days=7))
        await safe_reply(message, _format_events(events, "📅 <b>Неделя:</b>"))
        return

    if sub.startswith("add "):
        await _handle_add(message, gcal, sub[4:].strip())
        return

    if sub.startswith("del "):
        await _handle_del(message, gcal, sub[4:].strip())
        return

    # No exact match — parse with LLM
    parsed = await _parse_natural(sub, config)
    if parsed is None or parsed.get("action") == "unknown":
        await message.answer(
            "Не удалось понять команду. Примеры:\n"
            "/gcal — события на сегодня\n"
            "/gcal завтра — на завтра\n"
            "/gcal создай встречу на завтра в 15:00\n"
            "/gcal del &lt;id&gt; — удалить событие",
            parse_mode="HTML",
        )
        return

    action = parsed["action"]

    if action == "view":
        period = parsed.get("period", "today")
        today = _today()
        if period == "tomorrow":
            events = await gcal.get_events(today + timedelta(days=1), today + timedelta(days=2))
            await safe_reply(message, _format_events(events, "📅 <b>Завтра:</b>"))
        elif period == "week":
            events = await gcal.get_events(today, today + timedelta(days=7))
            await safe_reply(message, _format_events(events, "📅 <b>Неделя:</b>"))
        else:
            events = await gcal.get_events(today, today + timedelta(days=1))
            await safe_reply(message, _format_events(events, "📅 <b>Сегодня:</b>"))
        return

    if action == "add":
        date_str = parsed.get("date", "")
        start_time = parsed.get("start", "")
        end_time = parsed.get("end", "")
        summary = parsed.get("summary", "Событие")

        try:
            start = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            await message.answer("Не удалось разобрать дату/время.")
            return

        if end_time:
            try:
                end = datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M")
            except ValueError:
                end = start + timedelta(hours=1)
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
        return

    if action == "delete":
        event_id = parsed.get("event_id", "")
        await _handle_del(message, gcal, event_id)
        return


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
