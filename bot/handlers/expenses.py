from __future__ import annotations

import csv
import logging
from datetime import datetime
from io import StringIO

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message

from bot.database.repository import Repository
from bot.services.charts import create_week_chart, create_year_chart
from bot.utils import safe_reply

logger = logging.getLogger(__name__)
router = Router()


class ExpState(StatesGroup):
    adding = State()


# ── helpers ──────────────────────────────────────────────

def _parse_amount(text: str) -> float | None:
    """Parse a number from user input (supports comma as decimal separator)."""
    text = text.strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


async def _ensure_settings(repo: Repository, user_id: int) -> dict:
    """Return finance_settings for user, creating defaults if needed."""
    settings = await repo.get_finance_settings(user_id)
    if settings is None:
        await repo.upsert_finance_settings(user_id, 0, 1, datetime.now().year)
        settings = await repo.get_finance_settings(user_id)
    return settings


def _fmt(val: float) -> str:
    """Format number with thousands separator."""
    if val == int(val):
        return f"{int(val):,}".replace(",", " ")
    return f"{val:,.2f}".replace(",", " ")


# ── /exp — enter expense mode ────────────────────────────

@router.message(Command("exp"))
async def cmd_exp(message: Message, state: FSMContext, repo: Repository) -> None:
    user_id = message.from_user.id
    settings = await _ensure_settings(repo, user_id)
    week = settings["current_week"]
    year = settings["current_year"]
    budget = settings["weekly_budget"]

    records = await repo.get_week_expenses(user_id, week, year)
    total = sum(r["amount"] for r in records)

    lines = [
        f"<b>Режим добавления расходов</b>",
        f"Неделя {week}, {year} год",
        f"Записей: {len(records)}, потрачено: {_fmt(total)} руб.",
    ]
    if budget > 0:
        remaining = budget - total
        lines.append(f"Бюджет: {_fmt(budget)}, осталось: {_fmt(remaining)} руб.")
    lines.append("\nОтправляйте числа — каждое станет расходом.")
    lines.append("Для выхода: /cancel")

    await state.set_state(ExpState.adding)
    await safe_reply(message, "\n".join(lines))


@router.message(Command("cancel"), StateFilter(ExpState.adding))
async def cmd_cancel_exp(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Режим расходов отключён.")


@router.message(ExpState.adding, F.text)
async def handle_expense_input(message: Message, repo: Repository, state: FSMContext) -> None:
    amount = _parse_amount(message.text)
    if amount is None:
        await state.clear()
        await message.answer("Режим расходов отключён (получен не числовой ввод).")
        return

    user_id = message.from_user.id
    settings = await _ensure_settings(repo, user_id)
    week = settings["current_week"]
    year = settings["current_year"]
    budget = settings["weekly_budget"]

    await repo.add_expense(user_id, amount, week, year)

    records = await repo.get_week_expenses(user_id, week, year)
    total = sum(r["amount"] for r in records)

    lines = [f"+ {_fmt(amount)} руб. (неделя {week})"]

    if budget > 0:
        remaining = budget - total
        lines.append(f"Итого: {_fmt(total)} / {_fmt(budget)} — осталось {_fmt(remaining)} руб.")
        if remaining < 0:
            lines.append("\n<b>Бюджет превышен!</b>")
        elif remaining < budget * 0.2:
            lines.append("\nОсталось менее 20% бюджета!")

    await safe_reply(message, "\n".join(lines))


# ── /week [N] ────────────────────────────────────────────

@router.message(Command("week"))
async def cmd_week(message: Message, repo: Repository) -> None:
    user_id = message.from_user.id
    settings = await _ensure_settings(repo, user_id)

    parts = message.text.split()
    if len(parts) >= 3:
        try:
            week = int(parts[1])
            year = int(parts[2])
        except ValueError:
            await message.answer("Использование: /week [номер] [год]")
            return
    elif len(parts) == 2:
        try:
            week = int(parts[1])
        except ValueError:
            await message.answer("Использование: /week [номер] [год]")
            return
        year = settings["current_year"]
    else:
        week = settings["current_week"]
        year = settings["current_year"]
    records = await repo.get_week_expenses(user_id, week, year)

    if not records:
        await message.answer(f"Нет записей за неделю {week} ({year} г.)")
        return

    total = sum(r["amount"] for r in records)
    budget = await repo.get_budget_for_week(user_id, week, year)

    lines = [
        f"<b>Неделя {week}</b> ({year} г.)",
        f"Записей: {len(records)}",
        f"Итого: {_fmt(total)} руб.",
    ]
    if budget > 0:
        remaining = budget - total
        pct = (total / budget) * 100
        status = "exceeded" if remaining < 0 else "ok"
        icon = "🔴" if status == "exceeded" else "🟢"
        lines.append(f"{icon} Бюджет: {_fmt(budget)} | {pct:.0f}% | остаток: {_fmt(remaining)}")

    await safe_reply(message, "\n".join(lines))

    # send chart
    chart = create_week_chart(records, week, budget)
    photo = BufferedInputFile(chart.read(), filename=f"week_{week}.png")
    await message.answer_photo(photo)


# ── /year [YYYY] ─────────────────────────────────────────

@router.message(Command("year"))
async def cmd_year(message: Message, repo: Repository) -> None:
    user_id = message.from_user.id
    settings = await _ensure_settings(repo, user_id)

    parts = message.text.split()
    if len(parts) >= 2:
        try:
            year = int(parts[1])
        except ValueError:
            await message.answer("Использование: /year [YYYY]")
            return
    else:
        year = settings["current_year"]

    records = await repo.get_year_expenses(user_id, year)
    if not records:
        await message.answer(f"Нет записей за {year} год.")
        return

    total = sum(r["amount"] for r in records)
    budget = await repo.get_budget_for_week(user_id, 1, year)

    # group by week
    weekly: dict[int, float] = {}
    for r in records:
        weekly[r["custom_week"]] = weekly.get(r["custom_week"], 0) + r["amount"]

    avg_week = total / len(weekly) if weekly else 0

    # group by month
    monthly: dict[int, float] = {}
    for r in records:
        dt = r["created_at"]
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        monthly[dt.month] = monthly.get(dt.month, 0) + r["amount"]

    avg_month = total / len(monthly) if monthly else 0

    lines = [
        f"<b>Статистика за {year} год</b>",
        f"Записей: {len(records)}",
        f"Итого: {_fmt(total)} руб.",
        f"Недель: {len(weekly)}, в среднем: {_fmt(avg_week)} руб./нед.",
        f"Месяцев: {len(monthly)}, в среднем: {_fmt(avg_month)} руб./мес.",
    ]

    await safe_reply(message, "\n".join(lines))

    chart = create_year_chart(records, year, budget)
    photo = BufferedInputFile(chart.read(), filename=f"year_{year}.png")
    await message.answer_photo(photo)


# ── /budget [AMOUNT | list] ──────────────────────────────

@router.message(Command("budget"))
async def cmd_budget(message: Message, repo: Repository) -> None:
    user_id = message.from_user.id
    settings = await _ensure_settings(repo, user_id)
    parts = message.text.split()

    # /budget list
    if len(parts) >= 2 and parts[1].lower() == "list":
        history = await repo.get_budget_history(user_id)
        if not history:
            await message.answer("История бюджета пуста.")
            return
        lines = ["<b>История бюджета:</b>"]
        for h in history:
            lines.append(f"  {_fmt(h['amount'])} руб. — с недели {h['week_from']} ({h['year_from']} г.)")
        lines.append(f"\nТекущий: {_fmt(settings['weekly_budget'])} руб.")
        await safe_reply(message, "\n".join(lines))
        return

    # /budget AMOUNT
    if len(parts) >= 2:
        amount = _parse_amount(parts[1])
        if amount is None or amount < 0:
            await message.answer("Укажите корректную сумму: /budget 25000")
            return
        old_budget = settings["weekly_budget"]
        if old_budget > 0:
            await repo.add_budget_history(
                user_id, old_budget, settings["current_week"], settings["current_year"]
            )
        await repo.upsert_finance_settings(
            user_id, amount, settings["current_week"], settings["current_year"]
        )
        await message.answer(
            f"Бюджет обновлён: {_fmt(old_budget)} → {_fmt(amount)} руб."
        )
        return

    # /budget (no args) — show current
    await message.answer(f"Текущий недельный бюджет: {_fmt(settings['weekly_budget'])} руб.")


@router.message(Command("budget_list"))
async def cmd_budget_list(message: Message, repo: Repository) -> None:
    user_id = message.from_user.id
    history = await repo.get_budget_history(user_id)
    if not history:
        await message.answer("История бюджета пуста.")
        return
    settings = await _ensure_settings(repo, user_id)
    lines = ["<b>История бюджета:</b>"]
    for h in history:
        lines.append(f"  {_fmt(h['amount'])} руб. — с недели {h['week_from']} ({h['year_from']} г.)")
    lines.append(f"\nТекущий: {_fmt(settings['weekly_budget'])} руб.")
    await safe_reply(message, "\n".join(lines))


# ── /exp_latest ──────────────────────────────────────────

@router.message(Command("exp_latest"))
async def cmd_exp_latest(message: Message, repo: Repository) -> None:
    user_id = message.from_user.id
    records = await repo.get_latest_expenses(user_id, limit=10)
    if not records:
        await message.answer("Расходов пока нет.")
        return

    lines = ["<b>Последние 10 записей:</b>\n"]
    for r in records:
        dt = r["created_at"]
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        lines.append(
            f"<code>#{r['id']}</code> {dt.strftime('%d.%m %H:%M')} — "
            f"<b>{_fmt(r['amount'])}</b> руб. (нед. {r['custom_week']})"
        )
    await safe_reply(message, "\n".join(lines))


# ── /newweek ─────────────────────────────────────────────

@router.message(Command("newweek"))
async def cmd_newweek(message: Message, repo: Repository) -> None:
    user_id = message.from_user.id
    settings = await _ensure_settings(repo, user_id)
    week = settings["current_week"] + 1
    year = settings["current_year"]

    if week > 52:
        week = 1
        year += 1
        text = f"С новым {year} финансовым годом! Неделя 1."
    else:
        text = f"Начата неделя {week} ({year} г.)"

    await repo.upsert_finance_settings(user_id, settings["weekly_budget"], week, year)
    await message.answer(text)


# ── /export (expenses CSV) ───────────────────────────────

@router.message(Command("fexport"))
async def cmd_fexport(message: Message, repo: Repository) -> None:
    user_id = message.from_user.id
    records = await repo.get_all_expenses(user_id)
    if not records:
        await message.answer("Нет записей для экспорта.")
        return

    buf = StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Дата", "Сумма", "Неделя", "Год"])
    for r in records:
        writer.writerow([r["created_at"], r["amount"], r["custom_week"], r["year"]])

    total = sum(r["amount"] for r in records)
    writer.writerow([])
    writer.writerow(["ИТОГО", total, "", ""])

    data = buf.getvalue().encode("utf-8-sig")
    doc = BufferedInputFile(data, filename="expenses.csv")
    await message.answer_document(
        doc,
        caption=f"Записей: {len(records)}, итого: {_fmt(total)} руб.",
    )
