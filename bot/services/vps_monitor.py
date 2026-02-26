from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from aiogram import Bot

from bot.config import Config
from bot.database.repository import Repository

logger = logging.getLogger(__name__)

# One SSH session runs several lightweight commands
METRICS_CMD = r"""
awk '/cpu /{t=$2+$3+$4+$5+$6+$7+$8;i=$5;printf "%.1f",100*(t-i)/t}' /proc/stat
echo ""
awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{printf "%.1f %d %d",100*(t-a)/t,(t-a)/1024,t/1024}' /proc/meminfo
echo ""
df / | awk 'NR==2{gsub(/%/,"");printf "%s %.4f %.4f",$5,$3/1048576,$2/1048576}'
echo ""
awk '{printf "%d",$1}' /proc/uptime
echo ""
cat /proc/loadavg
echo ""
docker ps -a --format "{{.Names}}|{{.Status}}|{{.Image}}" 2>/dev/null || echo "DOCKER_UNAVAILABLE"
""".strip()


def _parse_metrics(stdout: str) -> dict:
    lines = stdout.strip().splitlines()
    # expected order: cpu, mem (3 fields), disk (3 fields), uptime, loadavg, docker lines...
    cpu_pct = float(lines[0]) if len(lines) > 0 else None

    mem_parts = lines[1].split() if len(lines) > 1 else []
    mem_pct = float(mem_parts[0]) if mem_parts else None
    mem_used_mb = int(mem_parts[1]) if len(mem_parts) > 1 else None
    mem_total_mb = int(mem_parts[2]) if len(mem_parts) > 2 else None

    disk_parts = lines[2].split() if len(lines) > 2 else []
    disk_pct = float(disk_parts[0]) if disk_parts else None
    disk_used_gb = float(disk_parts[1]) if len(disk_parts) > 1 else None
    disk_total_gb = float(disk_parts[2]) if len(disk_parts) > 2 else None

    uptime_sec = int(lines[3]) if len(lines) > 3 else None

    load_parts = lines[4].split() if len(lines) > 4 else []
    load_1 = float(load_parts[0]) if load_parts else None
    load_5 = float(load_parts[1]) if len(load_parts) > 1 else None
    load_15 = float(load_parts[2]) if len(load_parts) > 2 else None

    containers_json = None
    if len(lines) > 5:
        docker_lines = lines[5:]
        if docker_lines and docker_lines[0] != "DOCKER_UNAVAILABLE":
            containers = []
            for dl in docker_lines:
                if not dl.strip():
                    continue
                parts = dl.split("|")
                if len(parts) >= 3:
                    containers.append({
                        "name": parts[0],
                        "status": parts[1],
                        "image": parts[2],
                    })
            containers_json = json.dumps(containers, ensure_ascii=False)

    return {
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "mem_used_mb": mem_used_mb,
        "mem_total_mb": mem_total_mb,
        "disk_pct": disk_pct,
        "disk_used_gb": disk_used_gb,
        "disk_total_gb": disk_total_gb,
        "uptime_sec": uptime_sec,
        "load_1": load_1,
        "load_5": load_5,
        "load_15": load_15,
        "containers_json": containers_json,
    }


class VpsMonitorService:
    """Background service that polls VPS servers via SSH and sends alerts."""

    ALERT_COOLDOWN = timedelta(hours=1)

    def __init__(self, bot: Bot, repo: Repository, config: Config) -> None:
        self._bot = bot
        self._repo = repo
        self._config = config
        self._task: asyncio.Task | None = None
        self._last_alert: dict[str, datetime] = {}

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("VPS monitor started, interval=%ds", self._config.vps_poll_interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                servers = await self._repo.get_vps_servers()
                if servers:
                    results = await asyncio.gather(
                        *[self._poll_server(s) for s in servers],
                        return_exceptions=True,
                    )
                    for server, result in zip(servers, results):
                        alias = server["alias"]
                        if isinstance(result, Exception):
                            logger.warning("VPS poll failed for %s: %s", alias, result)
                            await self._maybe_alert(
                                alias,
                                f"🔴 Сервер <b>{alias}</b> недоступен!\n<code>{result}</code>",
                            )
                        else:
                            await self._repo.add_vps_metric(server["id"], **result)
                            await self._check_thresholds(server, result)
                            await self._check_container_changes(server, result)
            except Exception:
                logger.exception("VPS monitor loop error")

            await asyncio.sleep(self._config.vps_poll_interval)

    async def _poll_server(self, server: dict) -> dict:
        import asyncssh  # lazy import — optional dependency

        async with asyncssh.connect(
            server["host"],
            port=server["port"],
            username=server["user"],
            client_keys=[self._config.vps_ssh_key_path],
            known_hosts=None,
            connect_timeout=15,
        ) as conn:
            result = await conn.run(METRICS_CMD, check=True, timeout=30)
            return _parse_metrics(result.stdout)

    async def _maybe_alert(self, key: str, text: str) -> None:
        if not self._config.admin_id:
            return
        now = datetime.utcnow()
        last = self._last_alert.get(key)
        if last and now - last < self.ALERT_COOLDOWN:
            return
        self._last_alert[key] = now
        try:
            await self._bot.send_message(self._config.admin_id, text, parse_mode="HTML")
        except Exception:
            logger.exception("Failed to send VPS alert for %s", key)

    async def _check_thresholds(self, server: dict, metrics: dict) -> None:
        alias = server["alias"]
        alerts = []

        cpu = metrics.get("cpu_pct")
        if cpu is not None and cpu >= self._config.vps_cpu_threshold:
            alerts.append(f"CPU {cpu:.0f}% (порог {self._config.vps_cpu_threshold:.0f}%)")

        mem = metrics.get("mem_pct")
        if mem is not None and mem >= self._config.vps_mem_threshold:
            alerts.append(f"RAM {mem:.0f}% (порог {self._config.vps_mem_threshold:.0f}%)")

        disk = metrics.get("disk_pct")
        if disk is not None and disk >= self._config.vps_disk_threshold:
            alerts.append(f"Disk {disk:.0f}% (порог {self._config.vps_disk_threshold:.0f}%)")

        if alerts:
            text = f"⚠️ <b>{alias}</b> — превышены пороги:\n" + "\n".join(f"• {a}" for a in alerts)
            await self._maybe_alert(f"{alias}:thresholds", text)

    async def _check_container_changes(self, server: dict, metrics: dict) -> None:
        """Alert when a container goes from running to exited/dead."""
        new_json = metrics.get("containers_json")
        if not new_json:
            return

        prev = await self._repo.get_vps_latest_metric(server["id"])
        if not prev or not prev.get("containers_json"):
            return

        try:
            old_containers = {c["name"]: c["status"] for c in json.loads(prev["containers_json"])}
            new_containers = {c["name"]: c for c in json.loads(new_json)}
        except (json.JSONDecodeError, KeyError):
            return

        alerts = []
        for name, new_c in new_containers.items():
            old_status = old_containers.get(name, "")
            new_status = new_c.get("status", "")
            was_running = "up" in old_status.lower() or "running" in old_status.lower()
            is_down = any(s in new_status.lower() for s in ("exited", "dead", "removing"))
            if was_running and is_down:
                alerts.append(f"📦 <code>{name}</code> → {new_status}")

        if alerts:
            alias = server["alias"]
            text = (
                f"🔴 <b>{alias}</b> — контейнеры упали:\n"
                + "\n".join(alerts)
            )
            await self._maybe_alert(f"{alias}:containers", text)
