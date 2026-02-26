from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Union

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import Config
from bot.database.repository import Repository

logger = logging.getLogger(__name__)

# track pending requests so we don't spam admin
_pending_users: set[int] = set()


class AuthMiddleware(BaseMiddleware):
    def __init__(self, config: Config) -> None:
        self._admin_id = config.admin_id

    async def __call__(
        self,
        handler: Callable[[Union[Message, CallbackQuery], dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: dict[str, Any],
    ) -> Any:
        if not event.from_user:
            return

        user_id = event.from_user.id
        repo: Repository = data["repo"]

        # admin always has access
        if self._admin_id and user_id == self._admin_id:
            return await handler(event, data)

        # check if user is approved
        if await repo.is_user_approved(user_id):
            return await handler(event, data)

        # user not approved — save them and notify admin
        user = event.from_user
        await repo.upsert_user(user.id, user.username, user.first_name)

        if self._admin_id and user_id not in _pending_users:
            _pending_users.add(user_id)
            bot: Bot = event.bot
            username = f"@{user.username}" if user.username else "no username"
            await bot.send_message(
                self._admin_id,
                f"🆕 New user wants access:\n\n"
                f"Name: {user.first_name or ''} {user.last_name or ''}\n"
                f"Username: {username}\n"
                f"ID: <code>{user.id}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Approve", callback_data=f"approve:{user.id}"),
                        InlineKeyboardButton(text="❌ Deny", callback_data=f"deny:{user.id}"),
                    ]
                ]),
            )

        await event.answer("⏳ Your access request has been sent to the admin. Please wait for approval.")
