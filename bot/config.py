from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    openai_api_key: str
    openai_base_url: str
    default_model: str
    available_models: list[str]
    max_context_messages: int
    max_context_tokens: int
    allowed_users: list[int]
    whisper_model: str
    image_model: str
    admin_id: int | None
    daily_token_limit: int
    monthly_token_limit: int
    db_path: str
    google_credentials_path: str | None
    google_calendar_id: str | None
    timezone: str
    gcal_daily_hour: int
    gemini_api_keys: list[str]
    gemini_model: str
    weather_lat: float
    weather_lon: float
    weather_city: str
    vps_ssh_key_path: str
    vps_cpu_threshold: float
    vps_disk_threshold: float
    vps_mem_threshold: float
    vps_poll_interval: int

    @classmethod
    def from_env(cls) -> Config:
        allowed = os.getenv("ALLOWED_USERS", "").strip()
        allowed_users = [int(uid) for uid in allowed.split(",") if uid.strip()] if allowed else []

        admin_raw = os.getenv("ADMIN_ID", "").strip()
        admin_id = int(admin_raw) if admin_raw else None

        models_raw = os.getenv("AVAILABLE_MODELS", "").strip()
        default_model = os.getenv("DEFAULT_MODEL", "gpt-4o")
        if models_raw:
            available_models = [m.strip() for m in models_raw.split(",") if m.strip()]
        else:
            available_models = [default_model]

        return cls(
            telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            openai_api_key=os.environ["OPENAI_API_KEY"],
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            default_model=default_model,
            available_models=available_models,
            max_context_messages=int(os.getenv("MAX_CONTEXT_MESSAGES", "50")),
            max_context_tokens=int(os.getenv("MAX_CONTEXT_TOKENS", "8000")),
            allowed_users=allowed_users,
            whisper_model=os.getenv("WHISPER_MODEL", "whisper-1"),
            image_model=os.getenv("IMAGE_MODEL", "dall-e-3"),
            admin_id=admin_id,
            daily_token_limit=int(os.getenv("DAILY_TOKEN_LIMIT", "0")),
            monthly_token_limit=int(os.getenv("MONTHLY_TOKEN_LIMIT", "0")),
            db_path=os.getenv("DB_PATH", "/data/bot.db"),
            google_credentials_path=os.getenv("GOOGLE_CREDENTIALS_PATH") or None,
            google_calendar_id=os.getenv("GOOGLE_CALENDAR_ID") or None,
            timezone=os.getenv("TIMEZONE", "UTC"),
            gcal_daily_hour=int(os.getenv("GCAL_DAILY_HOUR", "8")),
            gemini_api_keys=[k.strip() for k in (os.getenv("GEMINI_API_KEY") or "").split(",") if k.strip()],
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            weather_lat=float(os.getenv("WEATHER_LAT", "55.1540")),
            weather_lon=float(os.getenv("WEATHER_LON", "61.4291")),
            weather_city=os.getenv("WEATHER_CITY", "Челябинск"),
            vps_ssh_key_path=os.getenv("VPS_SSH_KEY_PATH", "/data/keys/monitor_id_ed25519"),
            vps_cpu_threshold=float(os.getenv("VPS_CPU_THRESHOLD", "85.0")),
            vps_disk_threshold=float(os.getenv("VPS_DISK_THRESHOLD", "90.0")),
            vps_mem_threshold=float(os.getenv("VPS_MEM_THRESHOLD", "90.0")),
            vps_poll_interval=int(os.getenv("VPS_POLL_INTERVAL", "300")),
        )
