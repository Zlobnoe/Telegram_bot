from __future__ import annotations

import json
import logging
import re
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from openai import AsyncOpenAI

from bot.config import Config
from bot.database.repository import Repository
from bot.services.gcal import GCalRegistry, GCalService
from bot.services.stt import STTService

logger = logging.getLogger(__name__)
router = Router()

NOT_CONFIGURED = (
    "Google Calendar не настроен. Добавьте GOOGLE_CREDENTIALS_PATH в .env\n"
    "и настройте свой календарь командой /gcal addcal <calendar_id> [Название]"
)
NO_ACTIVE_CALENDAR = (
    "У вас нет активного календаря.\n"
    "Добавьте календарь: /gcal addcal <calendar_id> [Название]\n"
    "Список ваших календарей: /gcal calendars"
)


class GCalState(StatesGroup):
    waiting = State()


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


async def _get_user_gcal(
    user_id: int, repo: Repository, registry: GCalRegistry | None,
) -> GCalService | None:
    """Return the active GCalService for the user, or None if not set up."""
    if registry is None:
        return None
    cal = await repo.get_active_user_calendar(user_id)
    if cal is None:
        return None
    return registry.get_service(cal["calendar_id"])


async def _parse_natural(text: str, config: Config) -> dict | None:
    """Use LLM to parse natural language into a structured gcal command."""
    client = AsyncOpenAI(api_key=config.openai_api_key, base_url=config.openai_base_url)
    now = _now_local(config.timezone).strftime(f"%Y-%m-%d %H:%M ({config.timezone})")
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


def _now_local(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def _today(tz_name: str) -> datetime:
    return _now_local(tz_name).replace(hour=0, minute=0, second=0, microsecond=0)


# ── Calendar management subcommands ────────────────────────────────────────────

async def _handle_list_calendars(
    message: Message, repo: Repository, registry: GCalRegistry | None,
) -> None:
    """Show all calendars for the user."""
    cals = await repo.list_user_calendars(message.from_user.id)
    if not cals:
        sa_email = registry.service_account_email if registry else None
        hint = (
            f"\n\nEmail сервисного аккаунта для выдачи доступа:\n<code>{sa_email}</code>"
            if sa_email else ""
        )
        await message.answer(
            "У вас нет добавленных календарей.\n\n"
            "Как добавить:\n"
            "1. Откройте Google Календарь → Настройки → Ваш календарь → "
            "«Предоставить доступ пользователям или группам»\n"
            "2. Добавьте email сервисного аккаунта с правом «Редактирование»\n"
            "3. Скопируйте ID календаря (в настройках, раздел «Интеграция календаря»)\n"
            "4. Введите: /gcal addcal <calendar_id> [Название]"
            + hint,
            parse_mode="HTML",
        )
        return

    lines = ["📅 <b>Ваши календари:</b>"]
    for cal in cals:
        active_mark = " ✅" if cal["is_active"] else ""
        lines.append(
            f"  <b>{cal['id']}.</b> {cal['name']}{active_mark}\n"
            f"       <code>{cal['calendar_id']}</code>"
        )
    lines.append(
        "\n/gcal usecal &lt;id&gt; — выбрать активный\n"
        "/gcal delcal &lt;id&gt; — удалить"
    )
    await message.answer("\n".join(lines), parse_mode="HTML")


async def _handle_add_calendar(
    message: Message, repo: Repository, registry: GCalRegistry | None, args: str,
) -> None:
    """Add a new calendar for the user: addcal <calendar_id> [Name]."""
    parts = args.split(maxsplit=1)
    if not parts:
        await message.answer(
            "Использование: /gcal addcal <calendar_id> [Название]\n"
            "Пример: /gcal addcal user@gmail.com Рабочий"
        )
        return

    calendar_id = parts[0].strip()
    name = parts[1].strip() if len(parts) > 1 else "Мой календарь"

    if registry is None:
        await message.answer(NOT_CONFIGURED)
        return

    # Verify we can reach the calendar before saving
    svc = registry.get_service(calendar_id)
    today = _today(registry._timezone if hasattr(registry, "_timezone") else "UTC")
    try:
        await svc.get_events(today, today + timedelta(days=1))
    except Exception as e:
        await message.answer(
            f"Не удалось подключиться к календарю <code>{calendar_id}</code>.\n"
            f"Убедитесь, что вы предоставили доступ сервисному аккаунту.\n\n"
            f"Ошибка: {e}",
            parse_mode="HTML",
        )
        return

    row_id = await repo.add_user_calendar(message.from_user.id, calendar_id, name)
    if row_id is None:
        await message.answer(
            f"Календарь <code>{calendar_id}</code> уже добавлен.", parse_mode="HTML"
        )
        return

    # Activate if this is the first calendar
    cals = await repo.list_user_calendars(message.from_user.id)
    if len(cals) == 1:
        await repo.set_active_user_calendar(message.from_user.id, row_id)
        await message.answer(
            f"✅ Календарь «{name}» добавлен и выбран как активный.",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"✅ Календарь «{name}» добавлен.\n"
            f"Чтобы сделать его активным: /gcal usecal {row_id}",
            parse_mode="HTML",
        )


async def _handle_use_calendar(
    message: Message, repo: Repository, args: str,
) -> None:
    """Switch active calendar by id."""
    if not args.strip().isdigit():
        await message.answer("Использование: /gcal usecal <id>\nID можно узнать в /gcal calendars")
        return

    cal_id = int(args.strip())
    ok = await repo.set_active_user_calendar(message.from_user.id, cal_id)
    if ok:
        cals = await repo.list_user_calendars(message.from_user.id)
        name = next((c["name"] for c in cals if c["id"] == cal_id), str(cal_id))
        await message.answer(f"✅ Активный календарь: «{name}»")
    else:
        await message.answer(f"Календарь с ID {cal_id} не найден.")


async def _handle_del_calendar(
    message: Message, repo: Repository, args: str,
) -> None:
    """Delete a calendar by id."""
    if not args.strip().isdigit():
        await message.answer("Использование: /gcal delcal <id>\nID можно узнать в /gcal calendars")
        return

    cal_id = int(args.strip())
    ok = await repo.delete_user_calendar(cal_id, message.from_user.id)
    if ok:
        # If deleted was active, try to activate first remaining
        active = await repo.get_active_user_calendar(message.from_user.id)
        if active is None:
            remaining = await repo.list_user_calendars(message.from_user.id)
            if remaining:
                await repo.set_active_user_calendar(message.from_user.id, remaining[0]["id"])
        await message.answer(f"🗑 Календарь {cal_id} удалён.")
    else:
        await message.answer(f"Календарь с ID {cal_id} не найден.")


# ── Main command handler ────────────────────────────────────────────────────────

@router.message(Command("gcal"))
async def cmd_gcal(
    message: Message, state: FSMContext, config: Config, repo: Repository,
    gcal_registry: GCalRegistry | None = None,
) -> None:
    text = (message.text or "").split(maxsplit=1)
    sub = text[1].strip() if len(text) > 1 else ""

    # Calendar management subcommands (no active calendar required)
    if sub == "calendars":
        await _handle_list_calendars(message, repo, gcal_registry)
        return

    if sub.startswith("addcal ") or sub == "addcal":
        args = sub[len("addcal"):].strip()
        await _handle_add_calendar(message, repo, gcal_registry, args)
        return

    if sub.startswith("usecal ") or sub == "usecal":
        args = sub[len("usecal"):].strip()
        await _handle_use_calendar(message, repo, args)
        return

    if sub.startswith("delcal ") or sub == "delcal":
        args = sub[len("delcal"):].strip()
        await _handle_del_calendar(message, repo, args)
        return

    # All other subcommands require an active calendar
    gcal = await _get_user_gcal(message.from_user.id, repo, gcal_registry)
    if gcal is None:
        if gcal_registry is None:
            await message.answer(NOT_CONFIGURED)
        else:
            await message.answer(NO_ACTIVE_CALENDAR)
        return

    # /gcal with no args — show today + enter waiting state
    if not sub:
        await _show_events(message, gcal, "today")
        await state.set_state(GCalState.waiting)
        await message.answer(
            "Отправьте текстом или голосом что сделать с календарём.\n"
            "Например: <i>встреча с руководством в четверг в 11:00</i>\n"
            "/cancel — отмена",
            parse_mode="HTML",
        )
        return

    # Bulk add: multiple lines each starting with "add <date> <time> <summary>"
    # Supports both "add ..." and "/gcal add ..." per line (user may paste raw commands)
    lines = sub.splitlines()
    add_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        ll = line.lower()
        if ll.startswith("/gcal add "):
            add_lines.append(line[len("/gcal add "):].strip())
        elif ll.startswith("/gcal "):
            rest = line[len("/gcal "):].strip()
            if rest.lower().startswith("add "):
                add_lines.append(rest[4:].strip())
        elif ll.startswith("add "):
            add_lines.append(line[4:].strip())
    if len(add_lines) > 1:
        await _handle_bulk_add(message, gcal, add_lines)
        return

    # Direct subcommands (no FSM needed)
    await _process_gcal_input(message, state, config, gcal, sub)


@router.message(Command("gcal_calendars"))
async def cmd_gcal_calendars(
    message: Message, repo: Repository,
    gcal_registry: GCalRegistry | None = None,
) -> None:
    await _handle_list_calendars(message, repo, gcal_registry)


@router.message(Command("gcal_tomorrow"))
async def cmd_gcal_tomorrow(
    message: Message, repo: Repository,
    gcal_registry: GCalRegistry | None = None,
) -> None:
    gcal = await _get_user_gcal(message.from_user.id, repo, gcal_registry)
    if gcal is None:
        await message.answer(NOT_CONFIGURED if gcal_registry is None else NO_ACTIVE_CALENDAR)
        return
    await _show_events(message, gcal, "tomorrow")


@router.message(Command("gcal_week"))
async def cmd_gcal_week(
    message: Message, repo: Repository,
    gcal_registry: GCalRegistry | None = None,
) -> None:
    gcal = await _get_user_gcal(message.from_user.id, repo, gcal_registry)
    if gcal is None:
        await message.answer(NOT_CONFIGURED if gcal_registry is None else NO_ACTIVE_CALENDAR)
        return
    await _show_events(message, gcal, "week")


@router.message(Command("cancel"), StateFilter(GCalState.waiting))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Режим календаря завершён.")


@router.message(StateFilter(GCalState.waiting), F.voice)
async def gcal_voice(
    message: Message, state: FSMContext, config: Config, repo: Repository,
    gcal_registry: GCalRegistry | None = None, stt: STTService | None = None,
) -> None:
    gcal = await _get_user_gcal(message.from_user.id, repo, gcal_registry)
    if gcal is None or stt is None:
        await state.clear()
        return

    typing = await message.answer("🎤 Распознаю...")
    try:
        file = await message.bot.get_file(message.voice.file_id)
        ogg_path = tempfile.mktemp(suffix=".ogg")
        await message.bot.download_file(file.file_path, ogg_path)
        text = await stt.transcribe(ogg_path)
    except Exception:
        logger.exception("Voice transcription error in gcal")
        await typing.edit_text("Не удалось распознать голосовое сообщение.")
        return

    await typing.edit_text(f"🎤 <i>{text}</i>\n\n⏳ Обрабатываю...", parse_mode="HTML")
    await _process_gcal_input(message, state, config, gcal, text)


@router.message(StateFilter(GCalState.waiting), F.text)
async def gcal_text(
    message: Message, state: FSMContext, config: Config, repo: Repository,
    gcal_registry: GCalRegistry | None = None,
) -> None:
    gcal = await _get_user_gcal(message.from_user.id, repo, gcal_registry)
    if gcal is None:
        await state.clear()
        return

    await _process_gcal_input(message, state, config, gcal, message.text.strip())


async def _process_gcal_input(
    message: Message, state: FSMContext, config: Config,
    gcal: GCalService, text: str,
) -> None:
    """Process calendar input — exact commands or natural language."""
    sub_lower = text.lower()

    if sub_lower in ("tomorrow", "завтра"):
        await _show_events(message, gcal, "tomorrow")
        await state.clear()
        return

    if sub_lower in ("week", "неделя", "неделю"):
        await _show_events(message, gcal, "week")
        await state.clear()
        return

    if sub_lower in ("today", "сегодня"):
        await _show_events(message, gcal, "today")
        await state.clear()
        return

    if text.startswith("add "):
        await _handle_add(message, gcal, text[4:].strip())
        await state.clear()
        return

    if text.startswith("del "):
        await _handle_del(message, gcal, text[4:].strip())
        await state.clear()
        return

    # Natural language — parse with LLM
    parsed = await _parse_natural(text, config)
    if parsed is None or parsed.get("action") == "unknown":
        await message.answer(
            "Не удалось понять. Попробуйте ещё раз, например:\n"
            "<i>встреча с клиентом в пятницу в 14:00</i>\n"
            "/cancel — выход",
            parse_mode="HTML",
        )
        return

    action = parsed["action"]

    if action == "view":
        await _show_events(message, gcal, parsed.get("period", "today"))
        await state.clear()
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

        await _create_event(message, gcal, summary, start, end)
        await state.clear()
        return

    if action == "delete":
        event_id = parsed.get("event_id", "")
        await _handle_del(message, gcal, event_id)
        await state.clear()
        return


async def _show_events(message: Message, gcal: GCalService, period: str) -> None:
    today = _today(gcal.timezone)
    if period == "tomorrow":
        date_from, date_to, title = today + timedelta(days=1), today + timedelta(days=2), "📅 <b>Завтра:</b>"
    elif period == "week":
        date_from, date_to, title = today, today + timedelta(days=7), "📅 <b>Неделя:</b>"
    else:
        date_from, date_to, title = today, today + timedelta(days=1), "📅 <b>Сегодня:</b>"

    try:
        events = await gcal.get_events(date_from, date_to)
    except Exception as e:
        logger.exception("Failed to get events")
        await message.answer(f"Ошибка при получении событий: {e}")
        return
    await message.answer(_format_events(events, title), parse_mode="HTML")


async def _create_event(
    message: Message, gcal: GCalService, summary: str, start: datetime, end: datetime,
) -> None:
    try:
        event = await gcal.create_event(summary, start, end)
    except Exception as e:
        logger.exception("Failed to create event")
        await message.answer(f"Ошибка при создании события: {e}")
        return
    eid = event.get("id", "")[:8]
    await message.answer(
        f"✅ Событие создано!\n"
        f"  {start.strftime('%Y-%m-%d %H:%M')} — {end.strftime('%H:%M')}\n"
        f"  {summary}\n"
        f"  ID: <code>{eid}</code>",
        parse_mode="HTML",
    )


async def _handle_bulk_add(message: Message, gcal: GCalService, args_list: list[str]) -> None:
    """Add events one by one (sequential) to avoid Google API SSL/timeout errors."""
    import asyncio

    status = await message.answer(f"⏳ Добавляю {len(args_list)} событий по одному...")

    _re = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?:-(\d{2}:\d{2}))?\s+(.+)")

    ok: list[tuple[str, str]] = []
    fail: list[tuple[str, str]] = []

    for i, args in enumerate(args_list):
        m = _re.match(args)
        if not m:
            fail.append((args, "неверный формат"))
            continue

        date_str, start_time, end_time, summary = m.groups()
        try:
            start = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            fail.append((args, "ошибка даты"))
            continue

        end = (
            datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M")
            if end_time else start + timedelta(hours=1)
        )

        for attempt in range(3):
            try:
                await gcal.create_event(summary, start, end)
                ok.append((summary, start.strftime("%Y-%m-%d %H:%M")))
                break
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2)
                else:
                    fail.append((summary, str(e)[:80]))

        # Small pause between requests to avoid overwhelming the API
        if i < len(args_list) - 1:
            await asyncio.sleep(0.5)

    lines = [f"<b>Добавлено {len(ok)}/{len(args_list)} событий:</b>\n"]
    for name, ts in ok:
        lines.append(f"  ✅ {ts} — {name}")
    if fail:
        lines.append("")
        for name, err in fail:
            lines.append(f"  ❌ {name}: {err}")

    await status.edit_text("\n".join(lines), parse_mode="HTML")


async def _handle_add(message: Message, gcal: GCalService, args: str) -> None:
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

    await _create_event(message, gcal, summary, start, end)


async def _handle_del(message: Message, gcal: GCalService, event_id: str) -> None:
    if not event_id:
        await message.answer("Использование: /gcal del <id>")
        return

    today = _today(gcal.timezone)
    try:
        events = await gcal.get_events(today - timedelta(days=30), today + timedelta(days=60))
    except Exception as e:
        logger.exception("Failed to search events for deletion")
        await message.answer(f"Ошибка при поиске события: {e}")
        return

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
