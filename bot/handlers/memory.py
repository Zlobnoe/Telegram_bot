from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.database.repository import Repository

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("memory"))
async def cmd_memory(message: Message, repo: Repository) -> None:
    """Show stored facts about the user."""
    facts = await repo.get_user_facts(message.from_user.id)
    if not facts:
        await message.answer(
            "Я пока ничего о вас не запомнил.\n"
            "Расскажите о себе, и я запомню важные факты автоматически.\n\n"
            "Или используйте: /remember <факт>"
        )
        return

    lines = ["🧠 Что я о вас помню:\n"]
    for f in facts:
        lines.append(f"• {f['fact']}  [#{f['id']}]")
    lines.append("\n/forget <id> — удалить факт\n/forget_all — очистить всё")
    await message.answer("\n".join(lines))


@router.message(Command("remember"))
async def cmd_remember(message: Message, repo: Repository) -> None:
    """Manually save a fact: /remember я вегетарианец"""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /remember <факт о себе>")
        return

    fact = parts[1].strip()
    await repo.add_user_fact(message.from_user.id, fact)
    await message.answer(f"✅ Запомнил: {fact}")


@router.message(Command("forget"))
async def cmd_forget(message: Message, repo: Repository) -> None:
    """Delete a fact: /forget 5"""
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /forget <id>")
        return

    fid = int(parts[1])
    deleted = await repo.delete_user_fact(fid, message.from_user.id)
    if deleted:
        await message.answer(f"Факт #{fid} удалён.")
    else:
        await message.answer("Факт не найден.")


@router.message(Command("forget_all"))
async def cmd_forget_all(message: Message, repo: Repository) -> None:
    """Ask for confirmation before clearing all memory."""
    facts = await repo.get_user_facts(message.from_user.id)
    if not facts:
        await message.answer("Нечего удалять — память уже пуста.")
        return

    await message.answer(
        f"Удалить все <b>{len(facts)}</b> фактов? Это действие необратимо.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Да, удалить всё", callback_data="forget_all_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="forget_all_cancel"),
        ]]),
    )


@router.callback_query(F.data == "forget_all_confirm")
async def cb_forget_all_confirm(callback: CallbackQuery, repo: Repository) -> None:
    count = await repo.clear_user_memory(callback.from_user.id)
    await callback.message.edit_text(f"🗑 Удалено {count} фактов. Память очищена.")
    await callback.answer()


@router.callback_query(F.data == "forget_all_cancel")
async def cb_forget_all_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Отменено. Память не изменена.")
    await callback.answer()
