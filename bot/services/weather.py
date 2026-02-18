from __future__ import annotations

import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

# WMO Weather interpretation codes → Russian description + emoji
_WMO: dict[int, str] = {
    0:  "☀️ Ясно",
    1:  "🌤 В основном ясно",
    2:  "⛅ Переменная облачность",
    3:  "☁️ Пасмурно",
    45: "🌫 Туман",
    48: "🌫 Гололёд/изморозь",
    51: "🌦 Морось слабая",
    53: "🌦 Морось",
    55: "🌦 Морось сильная",
    61: "🌧 Дождь слабый",
    63: "🌧 Дождь",
    65: "🌧 Дождь сильный",
    71: "🌨 Снег слабый",
    73: "🌨 Снег",
    75: "🌨 Снег сильный",
    77: "🌨 Снежная крупа",
    80: "🌦 Ливень слабый",
    81: "🌦 Ливень",
    82: "⛈ Сильный ливень",
    85: "🌨 Снегопад слабый",
    86: "🌨 Снегопад сильный",
    95: "⛈ Гроза",
    96: "⛈ Гроза с градом",
    99: "⛈ Гроза с сильным градом",
}

_DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

BASE_URL = "https://api.open-meteo.com/v1/forecast"


def _desc(code: int) -> str:
    return _WMO.get(code, f"Неизвестно ({code})")


class WeatherService:
    """Fetches weather from Open-Meteo (free, no API key required)."""

    def __init__(self, lat: float, lon: float, city: str, timezone: str) -> None:
        self._lat = lat
        self._lon = lon
        self._city = city
        self._timezone = timezone

    async def get_forecast_text(self) -> str:
        """Return formatted weather block: current conditions + 5-day forecast."""
        params = {
            "latitude": self._lat,
            "longitude": self._lon,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_sum",
            "timezone": self._timezone,
            "forecast_days": 6,
            "wind_speed_unit": "ms",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("Failed to fetch weather from Open-Meteo")
            return "🌡 Погода временно недоступна"

        cur = data.get("current", {})
        daily = data.get("daily", {})

        temp = cur.get("temperature_2m", "?")
        feels = cur.get("apparent_temperature", "?")
        code = cur.get("weather_code", 0)
        wind = cur.get("wind_speed_10m", "?")
        humidity = cur.get("relative_humidity_2m", "?")

        lines = [
            f"🌡 <b>Погода в {self._city}:</b>",
            f"{_desc(code)}, {temp}°C (ощущается {feels}°C)",
            f"Ветер: {wind} м/с · Влажность: {humidity}%",
            "",
            "<b>Прогноз на 5 дней:</b>",
        ]

        dates = daily.get("time", [])
        t_max = daily.get("temperature_2m_max", [])
        t_min = daily.get("temperature_2m_min", [])
        codes = daily.get("weather_code", [])
        precip = daily.get("precipitation_sum", [])

        for i in range(1, min(6, len(dates))):
            date = datetime.strptime(dates[i], "%Y-%m-%d")
            day = _DAYS_RU[date.weekday()]
            desc = _desc(codes[i] if i < len(codes) else 0)
            hi = t_max[i] if i < len(t_max) else "?"
            lo = t_min[i] if i < len(t_min) else "?"
            rain = precip[i] if i < len(precip) else 0
            rain_str = f", осадки {rain:.1f} мм" if rain else ""
            lines.append(f"{day} {date.strftime('%d.%m')}: {desc}, {lo}°..{hi}°{rain_str}")

        return "\n".join(lines)
