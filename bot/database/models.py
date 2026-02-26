SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    is_approved BOOLEAN DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    title        TEXT,
    model        TEXT NOT NULL,
    system_prompt TEXT DEFAULT 'You are a helpful assistant.',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active    BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content         TEXT NOT NULL,
    content_type    TEXT DEFAULT 'text',
    image_url       TEXT,
    tokens_used     INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    type       TEXT NOT NULL CHECK(type IN ('chat','image','stt','vision','tts','web_search')),
    model      TEXT,
    tokens_used INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    chat_id    INTEGER NOT NULL,
    text       TEXT NOT NULL,
    remind_at  TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent       BOOLEAN DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    fact       TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversations_user   ON conversations(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_api_usage_user        ON api_usage(user_id, type);
CREATE INDEX IF NOT EXISTS idx_api_usage_created     ON api_usage(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reminders_time        ON reminders(remind_at, sent);
CREATE INDEX IF NOT EXISTS idx_user_memory_user      ON user_memory(user_id);

CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    amount      REAL NOT NULL,
    custom_week INTEGER NOT NULL,
    year        INTEGER NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS finance_settings (
    user_id        INTEGER PRIMARY KEY REFERENCES users(id),
    weekly_budget  REAL DEFAULT 0,
    current_week   INTEGER DEFAULT 1,
    current_year   INTEGER DEFAULT 2026
);

CREATE TABLE IF NOT EXISTS budget_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    amount      REAL NOT NULL,
    week_from   INTEGER NOT NULL,
    year_from   INTEGER NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_expenses_user_week    ON expenses(user_id, year, custom_week);
CREATE INDEX IF NOT EXISTS idx_expenses_user_year    ON expenses(user_id, year);
CREATE INDEX IF NOT EXISTS idx_budget_history_user   ON budget_history(user_id, year_from, week_from);

CREATE TABLE IF NOT EXISTS user_calendars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    calendar_id TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT 'Мой календарь',
    is_active   BOOLEAN DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, calendar_id)
);

CREATE INDEX IF NOT EXISTS idx_user_calendars_user ON user_calendars(user_id);

CREATE TABLE IF NOT EXISTS news_sources (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    url        TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, url)
);

CREATE TABLE IF NOT EXISTS news_items (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL REFERENCES users(id),
    title    TEXT NOT NULL,
    url      TEXT NOT NULL,
    source   TEXT NOT NULL DEFAULT '',
    liked    INTEGER DEFAULT NULL,
    shown_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_news_sources_user ON news_sources(user_id);
CREATE INDEX IF NOT EXISTS idx_news_items_user   ON news_items(user_id, shown_at);
"""
