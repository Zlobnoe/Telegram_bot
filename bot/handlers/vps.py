from __future__ import annotations

import json
import logging
from io import BytesIO

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)

from bot.config import Config
from bot.database.repository import Repository
from bot.services.charts import create_vps_chart

logger = logging.getLogger(__name__)
router = Router()

DANGEROUS_KEYWORDS = ("rm ", "kill ", "reboot", "shutdown", "drop ", "mkfs", "dd ")


def _is_admin(user_id: int, config: Config) -> bool:
    return config.admin_id is not None and user_id == config.admin_id


def _uptime_str(uptime_sec: int | None) -> str:
    if uptime_sec is None:
        return "?"
    days = uptime_sec // 86400
    hours = (uptime_sec % 86400) // 3600
    if days > 0:
        return f"{days}d {hours}h"
    minutes = (uptime_sec % 3600) // 60
    return f"{hours}h {minutes}m"


def _status_icon(cpu: float | None, mem: float | None, disk: float | None,
                 config: Config) -> str:
    if any(
        v is not None and v >= t
        for v, t in (
            (cpu, config.vps_cpu_threshold),
            (mem, config.vps_mem_threshold),
            (disk, config.vps_disk_threshold),
        )
    ):
        return "🟡"
    return "🟢"


def _containers_text(containers_json: str | None) -> str:
    if not containers_json:
        return ""
    try:
        containers = json.loads(containers_json)
    except json.JSONDecodeError:
        return ""
    lines = []
    for c in containers:
        status = c.get("status", "").lower()
        is_up = "up" in status or "running" in status
        icon = "✅" if is_up else "❌"
        lines.append(f"  {icon} <code>{c['name']}</code>  {c.get('status','')}")
    return "\n".join(lines)


async def _render_summary(servers: list[dict], repo: Repository, config: Config) -> str:
    if not servers:
        return "Нет добавленных серверов.\nИспользуй <code>/vps add &lt;alias&gt; &lt;host&gt; &lt;user&gt;</code>"

    lines = ["<b>🖥 Мониторинг серверов</b>\n"]
    for s in servers:
        m = await repo.get_vps_latest_metric(s["id"])
        if m is None:
            lines.append(f"🔴 <b>{s['alias']}</b>  (нет данных)\n")
            continue

        icon = _status_icon(m.get("cpu_pct"), m.get("mem_pct"), m.get("disk_pct"), config)
        cpu = f"{m['cpu_pct']:.0f}%" if m.get("cpu_pct") is not None else "?"
        mem = f"{m['mem_pct']:.0f}%" if m.get("mem_pct") is not None else "?"
        disk = f"{m['disk_pct']:.0f}%" if m.get("disk_pct") is not None else "?"
        uptime = _uptime_str(m.get("uptime_sec"))

        lines.append(
            f"{icon} <b>{s['alias']}</b>  CPU {cpu} | RAM {mem} | Disk {disk} | up {uptime}"
        )
        containers = _containers_text(m.get("containers_json"))
        if containers:
            lines.append(containers)
        lines.append("")

    return "\n".join(lines).strip()


async def _render_detail(server: dict, repo: Repository, config: Config) -> str:
    m = await repo.get_vps_latest_metric(server["id"])
    if m is None:
        return f"<b>{server['alias']}</b> — нет данных. Ждите первого опроса."

    icon = _status_icon(m.get("cpu_pct"), m.get("mem_pct"), m.get("disk_pct"), config)
    lines = [
        f"{icon} <b>{server['alias']}</b>  ({server['host']}:{server['port']})",
        "",
        f"CPU:   <b>{m.get('cpu_pct', '?'):.1f}%</b>  (load {m.get('load_1','?')} / {m.get('load_5','?')} / {m.get('load_15','?')})",
        f"RAM:   <b>{m.get('mem_pct', 0):.1f}%</b>  ({m.get('mem_used_mb',0):,} / {m.get('mem_total_mb',0):,} MB)",
        f"Disk:  <b>{m.get('disk_pct', 0):.1f}%</b>  ({m.get('disk_used_gb',0):.2f} / {m.get('disk_total_gb',0):.2f} GB)",
        f"Uptime: {_uptime_str(m.get('uptime_sec'))}",
        f"Recorded: {m.get('recorded_at','')}",
    ]
    containers = _containers_text(m.get("containers_json"))
    if containers:
        lines.append("\n<b>Контейнеры:</b>")
        lines.append(containers)
    return "\n".join(lines)


# ── /vps ──────────────────────────────────────────────────

@router.message(Command("vps"))
async def cmd_vps(message: Message, repo: Repository, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return

    parts = message.text.split(maxsplit=3)
    # /vps                       → summary
    # /vps add <alias> <host> <user> [port]
    # /vps remove <alias>
    # /vps exec <alias> <cmd>
    # /vps <alias>               → detail + chart

    if len(parts) == 1:
        servers = await repo.get_vps_servers()
        text = await _render_summary(servers, repo, config)
        await message.answer(text, parse_mode="HTML")
        return

    sub = parts[1].lower()

    if sub == "add":
        await _cmd_vps_add(message, parts, repo)
        return

    if sub == "remove":
        await _cmd_vps_remove(message, parts, repo)
        return

    if sub == "exec":
        await _cmd_vps_exec(message, parts, repo, config)
        return

    # /vps <alias>
    alias = parts[1]
    server = await repo.get_vps_server_by_alias(alias)
    if not server:
        await message.answer(f"Сервер <code>{alias}</code> не найден.", parse_mode="HTML")
        return

    text = await _render_detail(server, repo, config)
    await message.answer(text, parse_mode="HTML")

    # send chart if we have metrics
    metrics = await repo.get_vps_metrics(server["id"], hours=24)
    if len(metrics) >= 2:
        buf: BytesIO = create_vps_chart(
            metrics, alias,
            cpu_threshold=config.vps_cpu_threshold,
            mem_threshold=config.vps_mem_threshold,
            disk_threshold=config.vps_disk_threshold,
        )
        photo = BufferedInputFile(buf.read(), filename=f"vps_{alias}.png")
        await message.answer_photo(photo)


async def _cmd_vps_add(message: Message, parts: list[str], repo: Repository) -> None:
    # /vps add <alias> <host> <user> [port]
    # parts: ['vps', 'add', '<alias> <host> <user> [port]'] or split differently
    # Re-parse from raw text for safety
    raw = message.text.split(maxsplit=1)[1]  # everything after /vps
    tokens = raw.split()
    # tokens[0] = 'add', [1]=alias, [2]=host, [3]=user, [4]=port (optional)
    if len(tokens) < 4:
        await message.answer(
            "Использование: <code>/vps add &lt;alias&gt; &lt;host&gt; &lt;user&gt; [port]</code>",
            parse_mode="HTML",
        )
        return

    alias = tokens[1]
    host = tokens[2]
    user = tokens[3]
    port = int(tokens[4]) if len(tokens) > 4 else 22

    try:
        await repo.add_vps_server(alias, host, port, user)
        await message.answer(
            f"✅ Сервер <b>{alias}</b> добавлен: {user}@{host}:{port}",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"Ошибка: <code>{e}</code>", parse_mode="HTML")


async def _cmd_vps_remove(message: Message, parts: list[str], repo: Repository) -> None:
    if len(parts) < 3:
        await message.answer(
            "Использование: <code>/vps remove &lt;alias&gt;</code>", parse_mode="HTML"
        )
        return

    alias = parts[2]
    deleted = await repo.delete_vps_server(alias)
    if deleted:
        await message.answer(f"🗑 Сервер <b>{alias}</b> удалён.", parse_mode="HTML")
    else:
        await message.answer(f"Сервер <code>{alias}</code> не найден.", parse_mode="HTML")


async def _cmd_vps_exec(message: Message, parts: list[str], repo: Repository, config: Config) -> None:
    # /vps exec <alias> <command...>
    raw = message.text.split(maxsplit=1)[1]  # after /vps
    tokens = raw.split(maxsplit=2)
    # tokens[0]=exec, [1]=alias, [2]=command
    if len(tokens) < 3:
        await message.answer(
            "Использование: <code>/vps exec &lt;alias&gt; &lt;команда&gt;</code>", parse_mode="HTML"
        )
        return

    alias = tokens[1]
    cmd = tokens[2]

    server = await repo.get_vps_server_by_alias(alias)
    if not server:
        await message.answer(f"Сервер <code>{alias}</code> не найден.", parse_mode="HTML")
        return

    # Confirm dangerous commands via inline button
    if any(kw in cmd.lower() for kw in DANGEROUS_KEYWORDS):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Выполнить",
                callback_data=f"vpsexec:{alias}:{cmd}",
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="vpsexec:cancel",
            ),
        ]])
        await message.answer(
            f"⚠️ Опасная команда на <b>{alias}</b>:\n<code>{cmd}</code>\n\nПодтвердить?",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    await _run_exec(message, server, cmd, config)


async def _run_exec(message: Message, server: dict, cmd: str, config: Config) -> None:
    try:
        import asyncssh  # lazy import
        async with asyncssh.connect(
            server["host"],
            port=server["port"],
            username=server["user"],
            client_keys=[config.vps_ssh_key_path],
            known_hosts=None,
            connect_timeout=15,
        ) as conn:
            result = await conn.run(cmd, timeout=60)
            output = (result.stdout or "") + (result.stderr or "")
            output = output.strip()[:4000] or "(нет вывода)"
    except Exception as e:
        output = f"Ошибка SSH: {e}"

    await message.answer(
        f"<b>{server['alias']}</b> $ <code>{cmd}</code>\n\n<pre>{output}</pre>",
        parse_mode="HTML",
    )


# ── callbacks for exec confirmation ───────────────────────

@router.callback_query(F.data.startswith("vpsexec:"))
async def cb_vpsexec(callback: CallbackQuery, repo: Repository, config: Config) -> None:
    if not _is_admin(callback.from_user.id, config):
        await callback.answer("Not authorized", show_alert=True)
        return

    data = callback.data[len("vpsexec:"):]
    if data == "cancel":
        await callback.message.edit_text("❌ Отменено.")
        await callback.answer()
        return

    # format: <alias>:<cmd>
    alias, _, cmd = data.partition(":")
    server = await repo.get_vps_server_by_alias(alias)
    if not server:
        await callback.message.edit_text(f"Сервер {alias} не найден.")
        await callback.answer()
        return

    await callback.message.edit_text(f"⏳ Выполняю на <b>{alias}</b>…", parse_mode="HTML")
    await callback.answer()
    await _run_exec(callback.message, server, cmd, config)
