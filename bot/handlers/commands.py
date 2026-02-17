from __future__ import annotations

import json
import io

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import Config
from bot.database.repository import Repository
from bot.utils import safe_reply

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, repo: Repository, config: Config) -> None:
    user = message.from_user
    await repo.upsert_user(user.id, user.username, user.first_name)

    conv = await repo.get_active_conversation(user.id)
    if conv is None:
        await repo.create_conversation(user.id, config.default_model)

    await message.answer(
        f"Hello, {user.first_name}! I'm your AI assistant.\n\n"
        "Send /help for the full command list.\n\n"
        "Just send a message, a URL, or a voice message!"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📋 Commands:\n\n"
        "Chat:\n"
        "/reset — new conversation\n"
        "/conversations — switch dialogs\n"
        "/history — recent messages\n"
        "/export — export to file\n"
        "/model — switch model\n"
        "/system — set system prompt\n\n"
        "Tools:\n"
        "/image — generate image\n"
        "/search — web search\n"
        "/sum <url> — summarize a page\n"
        "/stats — статистика (= /usage)\n\n"
        "Reminders:\n"
        "/remind — set reminder\n"
        "/reminders — list active\n"
        "/delremind <id> — delete\n\n"
        "Memory:\n"
        "/memory — what I know about you\n"
        "/remember <fact> — save a fact\n"
        "/forget <id> — delete fact\n"
        "/forget_all — clear memory\n\n"
        "Calendar:\n"
        "/gcal — events today\n"
        "/gcal tomorrow / week\n"
        "/gcal add <date> <time> <text>\n"
        "/gcal del <id>\n\n"
        "Finances:\n"
        "/exp — режим добавления расходов\n"
        "/week [N] — статистика за неделю\n"
        "/year [YYYY] — статистика за год\n"
        "/budget [сумма|list] — бюджет\n"
        "/newweek — новая финансовая неделя\n"
        "/fexport — экспорт расходов CSV\n\n"
        "Skills:\n"
        "/skills — installed skills\n"
        "/calc /time /run /translate /summarize\n\n"
        "Just send a message or a URL!"
    )


@router.message(Command("reset"))
async def cmd_reset(message: Message, repo: Repository, config: Config) -> None:
    await repo.create_conversation(message.from_user.id, config.default_model)
    await message.answer("Conversation reset. Starting fresh!")


@router.message(Command("history"))
async def cmd_history(message: Message, repo: Repository) -> None:
    conv = await repo.get_active_conversation(message.from_user.id)
    if conv is None:
        await message.answer("No active conversation.")
        return

    msgs = await repo.get_last_messages_formatted(conv["id"], limit=10)
    if not msgs:
        await message.answer("No messages yet.")
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
        await message.answer(f"Model switched to `{model_name}`", parse_mode="Markdown")
        return

    # show buttons
    conv = await repo.get_active_conversation(message.from_user.id)
    current = conv["model"] if conv else config.default_model

    buttons = []
    for model in config.available_models:
        label = f"✅ {model}" if model == current else model
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"model:{model}")])

    await message.answer(
        f"Current model: `{current}`\nSelect a model:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.message(Command("system"))
async def cmd_system(message: Message, repo: Repository, config: Config) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        conv = await repo.get_active_conversation(message.from_user.id)
        current = conv["system_prompt"] if conv else "You are a helpful assistant."
        await message.answer(f"Current system prompt:\n{current}\n\nUsage: /system <prompt>")
        return

    prompt = parts[1].strip()
    conv = await repo.get_active_conversation(message.from_user.id)
    if conv is None:
        await repo.create_conversation(message.from_user.id, config.default_model, system_prompt=prompt)
    else:
        await repo.update_conversation_system_prompt(conv["id"], prompt)
    await message.answer("System prompt updated.")


# ── /conversations — list & switch ────────────────────────

@router.message(Command("conversations"))
async def cmd_conversations(message: Message, repo: Repository) -> None:
    convs = await repo.get_user_conversations(message.from_user.id)
    if not convs:
        await message.answer("No conversations yet. Send a message to start one.")
        return

    buttons = []
    for c in convs:
        active = "▶ " if c["is_active"] else ""
        title = c["title"] or f"Chat #{c['id']}"
        label = f"{active}{title} ({c['message_count']} msgs)"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"conv:{c['id']}")])

    await message.answer(
        "Your conversations (tap to switch):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ── /export — download conversation ──────────────────────

@router.message(Command("export"))
async def cmd_export(message: Message, repo: Repository) -> None:
    conv = await repo.get_active_conversation(message.from_user.id)
    if conv is None:
        await message.answer("No active conversation to export.")
        return

    msgs = await repo.get_all_messages_for_export(conv["id"])
    if not msgs:
        await message.answer("No messages to export.")
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

    await message.answer_document(text_file, caption=f"Conversation #{conv['id']} (text)")
    await message.answer_document(json_file, caption=f"Conversation #{conv['id']} (JSON)")


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


@router.message(Command("usage", "stats"))
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
