from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, FSInputFile

from bot.config import Config
from bot.database.repository import Repository
from bot.services.llm import LLMService
from bot.services.tts import TTSService
from bot.utils import safe_edit

logger = logging.getLogger(__name__)
router = Router()


def _is_admin(user_id: int, config: Config) -> bool:
    return config.admin_id is not None and user_id == config.admin_id


# ── callback: approve / deny ──────────────────────────────

@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(callback: CallbackQuery, repo: Repository, config: Config) -> None:
    if not _is_admin(callback.from_user.id, config):
        await callback.answer("Not authorized", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    await repo.set_user_approved(target_id, True)

    user = await repo.get_user(target_id)
    name = user["first_name"] if user else str(target_id)

    await callback.message.edit_text(
        callback.message.text + "\n\n✅ Approved by admin",
    )
    await callback.answer(f"{name} approved")

    try:
        await callback.bot.send_message(target_id, "✅ Your access has been approved! Send /start to begin.")
    except Exception:
        logger.warning("Could not notify user %d", target_id)


@router.callback_query(F.data.startswith("deny:"))
async def cb_deny(callback: CallbackQuery, repo: Repository, config: Config) -> None:
    if not _is_admin(callback.from_user.id, config):
        await callback.answer("Not authorized", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])

    user = await repo.get_user(target_id)
    name = user["first_name"] if user else str(target_id)

    await callback.message.edit_text(
        callback.message.text + "\n\n❌ Denied by admin",
    )
    await callback.answer(f"{name} denied")

    try:
        await callback.bot.send_message(target_id, "❌ Your access request has been denied.")
    except Exception:
        logger.warning("Could not notify user %d", target_id)


# ── callback: model selection ─────────────────────────────

@router.callback_query(F.data.startswith("model:"))
async def cb_model(callback: CallbackQuery, repo: Repository, config: Config) -> None:
    model_name = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id

    conv = await repo.get_active_conversation(user_id)
    if conv is None:
        await repo.create_conversation(user_id, model_name)
    else:
        await repo.update_conversation_model(conv["id"], model_name)

    await callback.message.edit_text(f"Model switched to `{model_name}`", parse_mode="Markdown")
    await callback.answer(f"Model: {model_name}")


# ── callback: conversation switch ─────────────────────────

@router.callback_query(F.data.startswith("conv:"))
async def cb_conv(callback: CallbackQuery, repo: Repository) -> None:
    conv_id = int(callback.data.split(":")[1])
    success = await repo.switch_conversation(callback.from_user.id, conv_id)

    if success:
        await callback.message.edit_text(f"Switched to conversation #{conv_id}")
        await callback.answer("Switched!")
    else:
        await callback.answer("Conversation not found", show_alert=True)


# ── callback: retry ───────────────────────────────────────

@router.callback_query(F.data == "retry")
async def cb_retry(callback: CallbackQuery, llm: LLMService, config: Config) -> None:
    user_id = callback.from_user.id

    await callback.answer("Regenerating…")
    await callback.message.edit_text("🔄 Regenerating…")

    try:
        response = await llm.retry_last(user_id)
        if response is None:
            await callback.message.edit_text("Nothing to retry.")
            return
    except Exception:
        logger.exception("Retry error")
        await callback.message.edit_text("Failed to regenerate.")
        return

    from bot.handlers.chat import RETRY_KB

    await safe_edit(callback.message, response, reply_markup=RETRY_KB)


# ── callback: TTS last response ──────────────────────────

@router.callback_query(F.data == "tts_last")
async def cb_tts(callback: CallbackQuery, repo: Repository, tts: TTSService, config: Config) -> None:
    user_id = callback.from_user.id

    conv = await repo.get_active_conversation(user_id)
    if not conv:
        await callback.answer("No conversation", show_alert=True)
        return

    last = await repo.get_last_assistant_message(conv["id"])
    if not last:
        await callback.answer("No message to vocalize", show_alert=True)
        return

    await callback.answer("Generating voice…")

    try:
        # truncate for TTS (4096 char limit for most TTS APIs)
        text = last["content"][:4096]
        ogg_path = await tts.synthesize(text)
        await repo.log_api_usage(user_id, "tts", "tts-1")

        audio_file = FSInputFile(ogg_path)
        await callback.message.answer_voice(audio_file)

        Path(ogg_path).unlink(missing_ok=True)
    except Exception:
        logger.exception("TTS error")
        await callback.message.answer("Failed to generate voice.")


# ── /admin — all users stats ──────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message, repo: Repository, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return

    users = await repo.get_all_users_with_stats()
    if not users:
        await message.answer("No users yet.")
        return

    lines = ["**All users:**\n"]
    for u in users:
        status = "✅" if u["is_approved"] else "⛔"
        username = f"@{u['username']}" if u["username"] else "—"
        lines.append(
            f"{status} {u['first_name'] or '?'} ({username})\n"
            f"   ID: `{u['id']}`\n"
            f"   Requests: {u['total_requests']}, Tokens: {u['total_tokens']:,}"
        )

    await message.answer("\n".join(lines), parse_mode="Markdown")


# ── /stats <user_id> — detailed user stats ────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message, repo: Repository, config: Config) -> None:
    parts = message.text.split(maxsplit=1)

    # non-admin: show own usage stats
    if not _is_admin(message.from_user.id, config):
        from bot.handlers.commands import cmd_usage
        await cmd_usage(message, repo)
        return

    if len(parts) < 2:
        await message.answer("Usage: /stats <user\\_id>", parse_mode="Markdown")
        return

    try:
        target_id = int(parts[1].strip())
    except ValueError:
        await message.answer("Usage: /stats <user\\_id>", parse_mode="Markdown")
        return
    user = await repo.get_user(target_id)
    if not user:
        await message.answer("User not found.")
        return

    totals = await repo.get_user_token_usage(target_id)
    api_usage = await repo.get_api_usage_summary(target_id)
    daily = await repo.get_daily_tokens(target_id)
    monthly = await repo.get_monthly_tokens(target_id)

    api_stats = {row["type"]: row for row in api_usage}

    username = f"@{user['username']}" if user["username"] else "—"
    status = "✅ Approved" if user["is_approved"] else "⛔ Not approved"

    lines = [
        f"**Stats for {user['first_name'] or '?'}** ({username})",
        f"ID: `{target_id}`",
        f"Status: {status}",
        f"Joined: {user['created_at']}",
        "",
        f"Today: {daily:,} tokens",
        f"This month: {monthly:,} tokens",
        f"All time: {totals['total_tokens']:,} tokens",
        "",
        f"Messages sent: {totals['user_messages']}",
        f"Responses: {totals['assistant_messages']}",
    ]

    for t in ("chat", "vision", "image", "stt", "tts"):
        s = api_stats.get(t, {"count": 0, "total_tokens": 0})
        lines.append(f"  {t}: {s['count']} calls, {s['total_tokens']:,} tokens")

    convs = await repo.get_user_token_usage_by_conversation(target_id)
    if convs:
        lines.append("")
        lines.append("**Conversations:**")
        for c in convs:
            active = " ✅" if c["is_active"] else ""
            lines.append(f"• #{c['id']} `{c['model']}` — {c['tokens']:,} tok, {c['message_count']} msgs{active}")

    await message.answer("\n".join(lines), parse_mode="Markdown")


# ── /ban, /unban ──────────────────────────────────────────

@router.message(Command("ban"))
async def cmd_ban(message: Message, repo: Repository, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /ban <user\\_id>", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[1].strip())
    except ValueError:
        await message.answer("Usage: /ban <user\\_id>", parse_mode="Markdown")
        return
    await repo.set_user_approved(target_id, False)
    user = await repo.get_user(target_id)
    name = user["first_name"] if user else str(target_id)
    await message.answer(f"⛔ {name} (`{target_id}`) banned.", parse_mode="Markdown")


@router.message(Command("unban"))
async def cmd_unban(message: Message, repo: Repository, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /unban <user\\_id>", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[1].strip())
    except ValueError:
        await message.answer("Usage: /unban <user\\_id>", parse_mode="Markdown")
        return
    await repo.set_user_approved(target_id, True)
    user = await repo.get_user(target_id)
    name = user["first_name"] if user else str(target_id)
    await message.answer(f"✅ {name} (`{target_id}`) approved.", parse_mode="Markdown")


# ── /broadcast <text> ─────────────────────────────────────

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, repo: Repository, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /broadcast <message>")
        return

    text = parts[1].strip()
    users = await repo.get_all_users()
    sent = 0
    failed = 0

    for u in users:
        if u["id"] == config.admin_id:
            continue
        try:
            await message.bot.send_message(u["id"], f"📢 {text}")
            sent += 1
        except Exception:
            failed += 1

    await message.answer(f"Broadcast done: {sent} sent, {failed} failed.")
