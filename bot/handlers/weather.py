from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.services.weather import WeatherService
from bot.utils import safe_reply

router = Router()


@router.message(Command("weather"))
async def cmd_weather(message: Message, weather: WeatherService | None = None) -> None:
    if weather is None:
        await message.answer("Погода не настроена (не задан WEATHER_LAT/LON/CITY).")
        return
    text = await weather.get_forecast_text()
    await safe_reply(message, text)
