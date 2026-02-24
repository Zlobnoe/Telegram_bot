from __future__ import annotations

import html
import json
import io

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from bot.config import Config
from bot.database.repository import Repository
from bot.utils import safe_reply

router = Router()

# ── Inline menu structure ──────────────────────────────────

_SECTIONS = {
    "chat": (
        "💬 Чат",
        [
            ("/reset", "Сбросить контекст диалога"),
            ("/conversations", "Список и переключение диалогов"),
            ("/history", "Последние 10 сообщений"),
            ("/export", "Экспорт диалога в файл"),
            ("/model", "Сменить языковую модель"),
            ("/system", "Задать системный промпт"),
            ("/stats", "Статистика токенов"),
        ],
    ),
    "tools": (
        "🛠 Инструменты",
        [
            ("/image <описание>", "Генерация изображения"),
            ("/search <запрос>", "Поиск в интернете"),
            ("/sum <url>", "Пересказ страницы по URL"),
        ],
    ),
    "reminders": (
        "⏰ Напоминания",
        [
            ("/remind <время> <текст>", "Установить напоминание"),
            ("/reminders", "Список активных напоминаний"),
            ("/delremind <id>", "Удалить напоминание"),
        ],
    ),
    "memory": (
        "🧠 Память",
        [
            ("/memory", "Что бот знает о тебе"),
            ("/remember <факт>", "Запомнить факт вручную"),
            ("/forget <id>", "Удалить конкретный факт"),
            ("/forget_all", "Очистить всю память"),
        ],
    ),
    "calendar": (
        "📅 Календарь",
        [
            ("/gcal", "События на сегодня"),
            ("/gcal_tomorrow", "События на завтра"),
            ("/gcal_week", "События на неделю"),
            ("/gcal_calendars", "Список подключённых календарей"),
            ("/gcal add <дата> <время> <текст>", "Добавить событие"),
            ("/gcal del <id>", "Удалить событие"),
        ],
    ),
    "finances": (
        "💰 Финансы",
        [
            ("/exp", "Добавить расход"),
            ("/week [N]", "Статистика за N-ю неделю"),
            ("/year [YYYY]", "Статистика за год"),
            ("/budget <сумма>", "Установить недельный бюджет"),
            ("/budget_list", "История бюджета"),
            ("/newweek", "Новая финансовая неделя"),
            ("/fexport", "Экспорт расходов в CSV"),
        ],
    ),
    "skills": (
        "⚡ Скиллы",
        [
            ("/skills", "Список установленных скиллов"),
            ("/calc", "Калькулятор"),
            ("/time", "Текущее время и дата"),
            ("/run", "Выполнить Python-код"),
            ("/translate", "Перевести текст"),
            ("/summarize", "Суммаризация текста"),
        ],
    ),
}

_MAIN_MENU_TEXT = (
    "👋 Привет! Я твой AI-ассистент.\n\n"
    "Выбери раздел, чтобы посмотреть доступные команды:"
)


def _main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💬 Чат", callback_data="menu:chat"),
            InlineKeyboardButton(text="🛠 Инструменты", callback_data="menu:tools"),
        ],
        [
            InlineKeyboardButton(text="⏰ Напоминания", callback_data="menu:reminders"),
            InlineKeyboardButton(text="🧠 Память", callback_data="menu:memory"),
        ],
        [
            InlineKeyboardButton(text="📅 Календарь", callback_data="menu:calendar"),
            InlineKeyboardButton(text="💰 Финансы", callback_data="menu:finances"),
        ],
        [
            InlineKeyboardButton(text="⚡ Скиллы", callback_data="menu:skills"),
        ],
    ])


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")],
    ])


def _section_text(key: str) -> str:
    title, commands = _SECTIONS[key]
    lines = [f"<b>{title}</b>\n"]
    for cmd, desc in commands:
        # Commands with args (<mandatory> or [optional]) → <code> block (tap to copy on mobile)
        # Simple /commands → plain text (auto-linked by Telegram, tap to send)
        if '<' in cmd or '[' in cmd:
            lines.append(f"<code>{html.escape(cmd)}</code> — {desc}")
        else:
            lines.append(f"{cmd} — {desc}")
    return "\n".join(lines)


# ── /start ─────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, repo: Repository, config: Config) -> None:
    user = message.from_user
    await repo.upsert_user(user.id, user.username, user.first_name)

    conv = await repo.get_active_conversation(user.id)
    if conv is None:
        await repo.create_conversation(user.id, config.default_model)

    await message.answer(
        f"Привет, {user.first_name}! Я твой AI-ассистент.\n\n"
        "Просто напиши сообщение, отправь URL или голосовое — отвечу!\n\n"
        "Список команд — /help",
        reply_markup=_main_menu_kb(),
    )


# ── /help ──────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(_MAIN_MENU_TEXT, reply_markup=_main_menu_kb())


# ── menu callbacks ─────────────────────────────────────────

@router.callback_query(F.data == "menu:main")
async def cb_menu_main(callback: CallbackQuery) -> None:
    await callback.message.edit_text(_MAIN_MENU_TEXT, reply_markup=_main_menu_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("menu:"))
async def cb_menu_section(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    if key not in _SECTIONS:
        await callback.answer("Неизвестный раздел", show_alert=True)
        return

    await callback.message.edit_text(
        _section_text(key),
        reply_markup=_back_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ── /reset ─────────────────────────────────────────────────

@router.message(Command("reset"))
async def cmd_reset(message: Message, repo: Repository, config: Config) -> None:
    await repo.create_conversation(message.from_user.id, config.default_model)
    await message.answer("Диалог сброшен. Начинаем с чистого листа!")


# ── /history ───────────────────────────────────────────────

@router.message(Command("history"))
async def cmd_history(message: Message, repo: Repository) -> None:
    conv = await repo.get_active_conversation(message.from_user.id)
    if conv is None:
        await message.answer("Нет активного диалога.")
        return

    msgs = await repo.get_last_messages_formatted(conv["id"], limit=10)
    if not msgs:
        await message.answer("Сообщений пока нет.")
        return

    lines = []
    for m in msgs:
        role = "🧑" if m["role"] == "user" else "🤖"
        text = m["content"][:200]
        if len(m["content"]) > 200:
            text += "…"
        lines.append(f"{role} {text}")

    await message.answer("\n\n".join(lines))


# ── /model — inline keyboard picker ──────────────────────

@router.message(Command("model"))
async def cmd_model(message: Message, repo: Repository, config: Config) -> None:
    parts = message.text.split(maxsplit=1)

    # direct usage: /model gpt-4o
    if len(parts) >= 2:
        model_name = parts[1].strip()
        conv = await repo.get_active_conversation(message.from_user.id)
        if conv is None:
            await repo.create_conversation(message.from_user.id, model_name)
        else:
            await repo.update_conversation_model(conv["id"], model_name)
        await message.answer(f"Модель переключена на `{model_name}`", parse_mode="Markdown")
        return

    # show buttons
    conv = await repo.get_active_conversation(message.from_user.id)
    current = conv["model"] if conv else config.default_model

    buttons = []
    for model in config.available_models:
        label = f"✅ {model}" if model == current else model
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"model:{model}")])

    await message.answer(
        f"Текущая модель: `{current}`\nВыберите модель:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.message(Command("system"))
async def cmd_system(message: Message, repo: Repository, config: Config) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        conv = await repo.get_active_conversation(message.from_user.id)
        current = conv["system_prompt"] if conv else "You are a helpful assistant."
        await message.answer(f"Текущий системный промпт:\n{current}\n\nИспользование: /system <промпт>")
        return

    prompt = parts[1].strip()
    conv = await repo.get_active_conversation(message.from_user.id)
    if conv is None:
        await repo.create_conversation(message.from_user.id, config.default_model, system_prompt=prompt)
    else:
        await repo.update_conversation_system_prompt(conv["id"], prompt)
    await message.answer("Системный промпт обновлён.")


# ── /conversations — list & switch ────────────────────────

@router.message(Command("conversations"))
async def cmd_conversations(message: Message, repo: Repository) -> None:
    convs = await repo.get_user_conversations(message.from_user.id)
    if not convs:
        await message.answer("Диалогов пока нет. Отправьте сообщение, чтобы начать.")
        return

    buttons = []
    for c in convs:
        active = "▶ " if c["is_active"] else ""
        title = c["title"] or f"Чат #{c['id']}"
        label = f"{active}{title} ({c['message_count']} сообщ.)"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"conv:{c['id']}")])

    await message.answer(
        "Ваши диалоги (нажмите, чтобы переключиться):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ── /export — download conversation ──────────────────────

@router.message(Command("export"))
async def cmd_export(message: Message, repo: Repository) -> None:
    conv = await repo.get_active_conversation(message.from_user.id)
    if conv is None:
        await message.answer("Нет активного диалога для экспорта.")
        return

    msgs = await repo.get_all_messages_for_export(conv["id"])
    if not msgs:
        await message.answer("Сообщений для экспорта нет.")
        return

    # text format
    lines = []
    for m in msgs:
        role = "User" if m["role"] == "user" else "Assistant" if m["role"] == "assistant" else "System"
        lines.append(f"[{m['created_at']}] {role}:\n{m['content']}\n")

    text_content = "\n".join(lines)
    text_file = BufferedInputFile(
        text_content.encode("utf-8"),
        filename=f"conversation_{conv['id']}.txt",
    )

    # json format
    json_content = json.dumps(msgs, ensure_ascii=False, indent=2)
    json_file = BufferedInputFile(
        json_content.encode("utf-8"),
        filename=f"conversation_{conv['id']}.json",
    )

    await message.answer_document(text_file, caption=f"Диалог #{conv['id']} (текст)")
    await message.answer_document(json_file, caption=f"Диалог #{conv['id']} (JSON)")


# ── /usage, /stats ────────────────────────────────────────

def _format_usage_block(title: str, api_usage: list[dict]) -> str:
    """Format a usage summary block from api_usage rows."""
    stats = {row["type"]: row for row in api_usage}
    chat = stats.get("chat", {"count": 0, "total_tokens": 0})
    web = stats.get("web_search", {"count": 0, "total_tokens": 0})
    vision = stats.get("vision", {"count": 0, "total_tokens": 0})
    image = stats.get("image", {"count": 0, "total_tokens": 0})
    stt = stats.get("stt", {"count": 0, "total_tokens": 0})
    tts = stats.get("tts", {"count": 0, "total_tokens": 0})

    total_tokens = sum(s.get("total_tokens", 0) for s in stats.values())
    total_requests = sum(s.get("count", 0) for s in stats.values())

    lines = [f"<b>{title}</b>  ({total_tokens:,} tok, {total_requests} req)"]
    if chat["count"]:
        lines.append(f"  💬 Chat: {chat['count']} × {chat['total_tokens']:,} tok")
    if web["count"]:
        lines.append(f"  🌐 Web search: {web['count']} × {web['total_tokens']:,} tok")
    if vision["count"]:
        lines.append(f"  👁 Vision: {vision['count']} × {vision['total_tokens']:,} tok")
    if image["count"]:
        lines.append(f"  🎨 Images: {image['count']}")
    if stt["count"]:
        lines.append(f"  🎤 STT: {stt['count']}")
    if tts["count"]:
        lines.append(f"  🔊 TTS: {tts['count']}")
    if total_requests == 0:
        lines.append("  —")
    return "\n".join(lines)


@router.message(Command("usage"))
async def cmd_usage(message: Message, repo: Repository) -> None:
    user_id = message.from_user.id

    daily = await repo.get_daily_usage_summary(user_id)
    monthly = await repo.get_monthly_usage_summary(user_id)
    total = await repo.get_api_usage_summary(user_id)
    totals = await repo.get_user_token_usage(user_id)

    parts = [
        "📊 <b>Статистика использования</b>\n",
        _format_usage_block("Сегодня", daily),
        _format_usage_block("За месяц", monthly),
        _format_usage_block("За всё время", total),
        "",
        f"✉️ Сообщений: {totals['user_messages']} отпр. / {totals['assistant_messages']} получ.",
    ]

    convs = await repo.get_user_token_usage_by_conversation(user_id)
    if convs:
        parts.append("\n<b>Последние диалоги:</b>")
        for c in convs:
            active = " ✅" if c["is_active"] else ""
            parts.append(
                f"• <code>{c['model']}</code> — {c['tokens']:,} tok, "
                f"{c['message_count']} msgs{active}"
            )

    await safe_reply(message, "\n".join(parts))
