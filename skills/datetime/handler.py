"""DateTime skill — provides current date/time info."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

TIMEZONES = {
    "msk": ("Europe/Moscow", 3),
    "moscow": ("Europe/Moscow", 3),
    "москва": ("Europe/Moscow", 3),
    "мск": ("Europe/Moscow", 3),
    "utc": ("UTC", 0),
    "gmt": ("UTC", 0),
    "est": ("US/Eastern", -5),
    "pst": ("US/Pacific", -8),
    "cet": ("Europe/Berlin", 1),
    "jst": ("Asia/Tokyo", 9),
    "tokyo": ("Asia/Tokyo", 9),
    "london": ("Europe/London", 0),
    "лондон": ("Europe/London", 0),
    "new york": ("US/Eastern", -5),
    "нью-йорк": ("US/Eastern", -5),
    "chelyabinsk": ("Asia/Yekaterinburg", 5),
    "челябинск": ("Asia/Yekaterinburg", 5),
    "екатеринбург": ("Asia/Yekaterinburg", 5),
}


def execute(query: str, **kwargs) -> str:
    """Return current date/time, optionally for a specific timezone."""
    query_lower = query.lower()

    # find timezone in query
    tz_name = "Europe/Moscow"
    tz_offset = 3
    for key, (name, offset) in TIMEZONES.items():
        if key in query_lower:
            tz_name = name
            tz_offset = offset
            break

    now = datetime.now(timezone(timedelta(hours=tz_offset)))

    weekdays_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    return (
        f"Timezone: {tz_name} (UTC{tz_offset:+d})\n"
        f"Date: {now.strftime('%Y-%m-%d')}\n"
        f"Time: {now.strftime('%H:%M:%S')}\n"
        f"Day: {weekdays_ru[now.weekday()]}\n"
        f"ISO: {now.isoformat()}"
    )
