from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.database.repository import Repository
from bot.services.reminder import parse_remind_time

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("remind"))
async def cmd_remind(message: Message, repo: Repository) -> None:
    """Set a reminder: /remind через 30 минут купить молоко"""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Usage: /remind <when> <what>\n\n"
            "Examples:\n"
            "/remind через 30 минут купить молоко\n"
            "/remind через 2 часа позвонить маме\n"
            "/remind in 1 hour check email\n"
            "/remind через 1 день оплатить счёт"
        )
        return

    result = parse_remind_time(parts[1])
    if result is None:
        await message.answer(
            "Не могу понять время. Используй формат:\n"
            "/remind через 5 минут текст\n"
            "/remind через 2 часа текст\n"
            "/remind in 30 minutes text"
        )
        return

    remind_at, reminder_text = result
    reminder_id = await repo.add_reminder(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        text=reminder_text,
        remind_at=remind_at.strftime("%Y-%m-%d %H:%M:%S"),
    )

    # format time nicely
    delta = remind_at - __import__("datetime").datetime.utcnow()
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        time_str = f"{total_seconds} сек"
    elif total_seconds < 3600:
        time_str = f"{total_seconds // 60} мин"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        mins = (total_seconds % 3600) // 60
        time_str = f"{hours} ч {mins} мин" if mins else f"{hours} ч"
    else:
        days = total_seconds // 86400
        time_str = f"{days} дн"

    await message.answer(
        f"✅ Напоминание #{reminder_id} установлено!\n"
        f"Через: {time_str}\n"
        f"Текст: {reminder_text}"
    )


@router.message(Command("reminders"))
async def cmd_reminders(message: Message, repo: Repository) -> None:
    """List active reminders."""
    reminders = await repo.get_user_reminders(message.from_user.id)
    if not reminders:
        await message.answer("У вас нет активных напоминаний.")
        return

    lines = ["Активные напоминания:\n"]
    for r in reminders:
        lines.append(f"#{r['id']} — {r['remind_at']}\n  {r['text']}")

    await message.answer("\n".join(lines))


@router.message(Command("delremind"))
async def cmd_del_remind(message: Message, repo: Repository) -> None:
    """Delete a reminder: /delremind 5"""
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /delremind <id>")
        return

    rid = int(parts[1])
    deleted = await repo.delete_reminder(rid, message.from_user.id)
    if deleted:
        await message.answer(f"Напоминание #{rid} удалено.")
    else:
        await message.answer("Напоминание не найдено.")
