from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from dotenv import load_dotenv

from bot.config import Config
from bot.database.repository import Repository
from bot.services.llm import LLMService
from bot.services.stt import STTService
from bot.services.tts import TTSService
from bot.services.skills import SkillsService
from bot.services.reminder import ReminderService
from bot.services.gcal import create_gcal_service, create_gcal_registry
from bot.services.gcal_digest import GCalDigestService
from bot.services.weather import WeatherService
from bot.middleware.auth import AuthMiddleware
from bot.handlers import commands, chat, voice, image, admin, vision, web, skills, reminder, summarize, memory, gcal, expenses, weather

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()
    config = Config.from_env()

    # database
    db_dir = os.path.dirname(config.db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    repo = Repository(config.db_path)
    await repo.connect()

    # services
    gemini = None
    if config.gemini_api_keys:
        from bot.services.gemini import GeminiService
        gemini = GeminiService(config, repo)
        logger.info("Gemini enabled: model=%s, keys=%d", config.gemini_model, len(config.gemini_api_keys))
    llm = LLMService(config, repo, gemini=gemini)
    stt = STTService(config)
    tts = TTSService(config)

    # skills
    skills_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")
    skill_service = SkillsService(skills_dir)
    await skill_service.load_all()
    llm.set_skills_prompt(skill_service.get_skills_prompt())

    # bot + dispatcher
    bot = Bot(token=config.telegram_bot_token)
    dp = Dispatcher()

    # middleware
    dp.message.middleware(AuthMiddleware(config))

    # inject dependencies
    dp["config"] = config
    dp["repo"] = repo
    dp["llm"] = llm
    dp["stt"] = stt
    dp["tts"] = tts
    dp["skill_service"] = skill_service

    # google calendar
    gcal_service = create_gcal_service(config.google_credentials_path, config.google_calendar_id, config.timezone)
    gcal_registry = create_gcal_registry(config.google_credentials_path, config.timezone)
    dp["gcal_registry"] = gcal_registry

    # reminder scheduler
    reminder_service = ReminderService(bot, repo)

    # register routers (order matters)
    dp.include_router(admin.router)      # callbacks + admin commands
    dp.include_router(expenses.router)   # /exp, /week, /year, /budget, /newweek, /fexport
    dp.include_router(commands.router)   # /start, /reset, etc.
    dp.include_router(gcal.router)       # /gcal
    dp.include_router(reminder.router)   # /remind, /reminders
    dp.include_router(memory.router)     # /memory, /remember, /forget
    dp.include_router(web.router)        # /search (before skills — skills catch-all eats /-commands)
    dp.include_router(image.router)      # /image
    dp.include_router(skills.router)     # /skills + skill triggers
    dp.include_router(weather.router)    # /weather
    dp.include_router(summarize.router)  # URL auto-summarize
    dp.include_router(voice.router)      # voice messages
    dp.include_router(vision.router)     # photo messages
    dp.include_router(chat.router)       # must be last — catches all text

    logger.info("Bot starting, model=%s", config.default_model)

    # Register commands for Telegram menu
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать диалог"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="reset", description="Сбросить контекст"),
        BotCommand(command="search", description="Поиск в интернете"),
        BotCommand(command="image", description="Сгенерировать изображение"),
        BotCommand(command="remind", description="Установить напоминание"),
        BotCommand(command="reminders", description="Список напоминаний"),
        BotCommand(command="memory", description="Моя память"),
        BotCommand(command="remember", description="Запомнить факт"),
        BotCommand(command="skills", description="Список навыков"),
        BotCommand(command="calc", description="Калькулятор"),
        BotCommand(command="time", description="Текущее время"),
        BotCommand(command="run", description="Выполнить Python код"),
        BotCommand(command="translate", description="Перевести текст"),
        BotCommand(command="summarize", description="Суммаризация текста"),
        BotCommand(command="sum", description="Суммаризация по URL"),
        BotCommand(command="gcal", description="Google Календарь (calendars/addcal/usecal/delcal)"),
        BotCommand(command="exp", description="Добавить расход"),
        BotCommand(command="week", description="Статистика за неделю"),
        BotCommand(command="year", description="Статистика за год"),
        BotCommand(command="budget", description="Недельный бюджет"),
        BotCommand(command="newweek", description="Новая финансовая неделя"),
        BotCommand(command="fexport", description="Экспорт расходов CSV"),
        BotCommand(command="stats", description="Статистика использования"),
        BotCommand(command="weather", description="Прогноз погоды"),
        BotCommand(command="exp_latest", description="Последние расходы"),
        BotCommand(command="delexp", description="Удалить расход"),
    ])

    # weather + daily digest (always runs, gcal is optional)
    weather_service = WeatherService(config.weather_lat, config.weather_lon, config.weather_city, config.timezone)
    dp["weather"] = weather_service
    gcal_digest = GCalDigestService(bot, config, gcal_service, weather_service)
    gcal_digest.start()

    reminder_service.start()

    try:
        await dp.start_polling(bot)
    finally:
        await gcal_digest.stop()
        await reminder_service.stop()
        await repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
