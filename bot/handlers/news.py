from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.database.repository import Repository
from bot.services.news import get_news_for_user

logger = logging.getLogger(__name__)
router = Router()

# ── helpers ──────────────────────────────────────────────

def _news_kb(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍", callback_data=f"news_like:{item_id}"),
        InlineKeyboardButton(text="👎", callback_data=f"news_dislike:{item_id}"),
    ]])


# ── /news_add <url> [name] ────────────────────────────────

@router.message(Command("news_add"))
async def cmd_news_add(message: Message, repo: Repository) -> None:
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            "Использование: /news_add &lt;url&gt; [название]\n"
            "Пример: /news_add https://habr.com/ru/rss/hubs/all/ Habr",
            parse_mode="HTML",
        )
        return

    url = parts[1].strip()
    name = parts[2].strip() if len(parts) > 2 else ""

    if not url.startswith(("http://", "https://")):
        await message.answer("URL должен начинаться с http:// или https://")
        return

    row_id = await repo.add_news_source(message.from_user.id, url, name)
    if row_id is None:
        await message.answer("Этот источник уже добавлен.")
        return

    display = html.escape(name or url)
    await message.answer(f"✅ Источник добавлен: <b>{display}</b>", parse_mode="HTML")


# ── /news_sources ─────────────────────────────────────────

@router.message(Command("news_sources"))
async def cmd_news_sources(message: Message, repo: Repository) -> None:
    sources = await repo.list_news_sources(message.from_user.id)
    if not sources:
        await message.answer(
            "У вас нет добавленных источников.\n"
            "Добавьте: /news_add &lt;url&gt; [название]",
            parse_mode="HTML",
        )
        return

    lines = ["<b>Ваши RSS-источники:</b>\n"]
    for s in sources:
        name = html.escape(s["name"] or s["url"])
        url = html.escape(s["url"])
        lines.append(f"  <code>#{s['id']}</code> <b>{name}</b>\n  {url}")
    lines.append("\n/news_remove &lt;id&gt; — удалить источник")
    await message.answer("\n".join(lines), parse_mode="HTML")


# ── /news_remove <id> ─────────────────────────────────────

@router.message(Command("news_remove"))
async def cmd_news_remove(message: Message, repo: Repository) -> None:
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: /news_remove &lt;id&gt;", parse_mode="HTML")
        return

    source_id = int(parts[1])
    deleted = await repo.delete_news_source(source_id, message.from_user.id)
    if deleted:
        await message.answer(f"Источник #{source_id} удалён.")
    else:
        await message.answer(f"Источник #{source_id} не найден.")


# ── /news ─────────────────────────────────────────────────

@router.message(Command("news"))
async def cmd_news(message: Message, repo: Repository) -> None:
    sources = await repo.list_news_sources(message.from_user.id)
    if not sources:
        await message.answer(
            "Нет источников. Добавьте: /news_add &lt;url&gt; [название]",
            parse_mode="HTML",
        )
        return

    wait_msg = await message.answer("🔄 Загружаю новости…")

    articles = await get_news_for_user(message.from_user.id, repo)

    if not articles:
        await wait_msg.edit_text("Нет новых статей за последние 24 часа.")
        return

    await wait_msg.delete()

    user_id = message.from_user.id
    for article in articles:
        item_id = await repo.add_news_item(
            user_id,
            title=article["title"],
            url=article["url"],
            source=article["source"],
        )
        title = html.escape(article["title"])
        source = html.escape(article["source"])
        raw_url = article["url"]
        if raw_url.startswith(("http://", "https://")):
            safe_url = html.escape(raw_url, quote=True)
        else:
            safe_url = "#"
        pub = article["published"].strftime("%H:%M")
        text = (
            f'<b>{title}</b>\n'
            f'<i>{source}</i> · {pub}\n'
            f'<a href="{safe_url}">Читать →</a>'
        )
        await message.answer(text, parse_mode="HTML", reply_markup=_news_kb(item_id))


# ── feedback callbacks ────────────────────────────────────

@router.callback_query(F.data.startswith("news_like:"))
async def cb_news_like(callback: CallbackQuery, repo: Repository) -> None:
    item_id = int(callback.data.split(":")[1])
    await repo.set_news_feedback(item_id, callback.from_user.id, liked=1)
    # Remove buttons to prevent duplicate votes
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("👍 Отмечено как интересное")


@router.callback_query(F.data.startswith("news_dislike:"))
async def cb_news_dislike(callback: CallbackQuery, repo: Repository) -> None:
    item_id = int(callback.data.split(":")[1])
    await repo.set_news_feedback(item_id, callback.from_user.id, liked=0)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("👎 Источник будет понижен в приоритете")
