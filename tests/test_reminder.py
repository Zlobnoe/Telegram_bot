from datetime import datetime, timedelta

from bot.services.reminder import parse_remind_time


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bounds(delta: timedelta):
    """Capture before/after around a function call to bracket expected time."""
    return datetime.utcnow(), delta


# ---------------------------------------------------------------------------
# Russian
# ---------------------------------------------------------------------------

def test_ru_30_minutes():
    before = datetime.utcnow()
    result = parse_remind_time("через 30 минут купить молоко")
    after = datetime.utcnow()

    assert result is not None
    dt, text = result
    assert text == "купить молоко"
    assert before + timedelta(minutes=30) <= dt <= after + timedelta(minutes=30)


def test_ru_2_hours():
    before = datetime.utcnow()
    result = parse_remind_time("через 2 часа позвонить")
    after = datetime.utcnow()

    assert result is not None
    dt, text = result
    assert text == "позвонить"
    assert before + timedelta(hours=2) <= dt <= after + timedelta(hours=2)


def test_ru_1_day():
    before = datetime.utcnow()
    result = parse_remind_time("через 1 день оплатить")
    after = datetime.utcnow()

    assert result is not None
    dt, text = result
    assert text == "оплатить"
    assert before + timedelta(days=1) <= dt <= after + timedelta(days=1)


def test_ru_10_seconds():
    before = datetime.utcnow()
    result = parse_remind_time("через 10 секунд тест")
    after = datetime.utcnow()

    assert result is not None
    dt, text = result
    assert text == "тест"
    assert before + timedelta(seconds=10) <= dt <= after + timedelta(seconds=10)


def test_ru_without_prefix():
    # "через" is optional in the regex
    before = datetime.utcnow()
    result = parse_remind_time("5 минут тест")
    after = datetime.utcnow()

    assert result is not None
    dt, text = result
    assert text == "тест"
    assert before + timedelta(minutes=5) <= dt <= after + timedelta(minutes=5)


# ---------------------------------------------------------------------------
# English
# ---------------------------------------------------------------------------

def test_en_30_minutes():
    before = datetime.utcnow()
    result = parse_remind_time("in 30 minutes do task")
    after = datetime.utcnow()

    assert result is not None
    dt, text = result
    assert text == "do task"
    assert before + timedelta(minutes=30) <= dt <= after + timedelta(minutes=30)


def test_en_2_hours():
    before = datetime.utcnow()
    result = parse_remind_time("in 2 hours check")
    after = datetime.utcnow()

    assert result is not None
    dt, text = result
    assert text == "check"
    assert before + timedelta(hours=2) <= dt <= after + timedelta(hours=2)


def test_en_1_day():
    before = datetime.utcnow()
    result = parse_remind_time("in 1 day review")
    after = datetime.utcnow()

    assert result is not None
    dt, text = result
    assert text == "review"
    assert before + timedelta(days=1) <= dt <= after + timedelta(days=1)


def test_en_5_seconds():
    before = datetime.utcnow()
    result = parse_remind_time("in 5 seconds ping")
    after = datetime.utcnow()

    assert result is not None
    dt, text = result
    assert text == "ping"
    assert before + timedelta(seconds=5) <= dt <= after + timedelta(seconds=5)


# ---------------------------------------------------------------------------
# Invalid input
# ---------------------------------------------------------------------------

def test_invalid_returns_none():
    assert parse_remind_time("завтра") is None


def test_empty_returns_none():
    assert parse_remind_time("") is None
