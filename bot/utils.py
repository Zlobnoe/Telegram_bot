from __future__ import annotations

import re
import logging
from aiogram.types import Message, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def md_to_html(text: str) -> str:
    """Convert common Markdown to Telegram HTML."""
    # code blocks ``` ... ```
    text = re.sub(r"```(\w*)\n(.*?)```", r"<pre>\2</pre>", text, flags=re.DOTALL)
    # inline code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # bold **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)
    # italic *text* or _text_ (but not inside words with underscores)
    text = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", text)
    # strikethrough ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    # links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    # headers ### text → bold
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    return text


async def safe_edit(msg: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Edit message with HTML formatting, fallback to plain text."""
    try:
        await msg.edit_text(md_to_html(text[:4096]), parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        try:
            await msg.edit_text(text[:4096], reply_markup=reply_markup)
        except Exception:
            pass


async def safe_reply(msg: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> Message:
    """Send message with HTML formatting, fallback to plain text."""
    try:
        return await msg.answer(md_to_html(text[:4096]), parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        return await msg.answer(text[:4096], reply_markup=reply_markup)
