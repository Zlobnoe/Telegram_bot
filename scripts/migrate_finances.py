#!/usr/bin/env python3
"""One-time migration: JSON financial records → SQLite.

Usage:
    python scripts/migrate_finances.py \
        --json financial_records_new.json \
        --settings settings.json \
        --db /data/bot.db \
        --user-id 123456789
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate financial records from JSON to SQLite")
    parser.add_argument("--json", required=True, help="Path to financial_records_new.json")
    parser.add_argument("--settings", required=True, help="Path to settings.json")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--user-id", required=True, type=int, help="Telegram user ID (owner)")
    args = parser.parse_args()

    # ── load JSON records ──
    json_path = Path(args.json)
    if not json_path.exists():
        print(f"ERROR: {json_path} not found")
        sys.exit(1)
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    records = data.get("records", [])
    print(f"Loaded {len(records)} records from {json_path}")

    # ── load settings ──
    settings_path = Path(args.settings)
    if not settings_path.exists():
        print(f"ERROR: {settings_path} not found")
        sys.exit(1)
    with open(settings_path, encoding="utf-8") as f:
        settings = json.load(f)
    weekly_budget = settings.get("weekly_budget", 0)
    current_week = settings.get("current_custom_week", 1)
    current_year = settings.get("current_financial_year", 2026)
    print(f"Settings: budget={weekly_budget}, week={current_week}, year={current_year}")

    # ── connect to DB ──
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: database {db_path} not found (run the bot once first to create schema)")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # ensure tables exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            amount      REAL NOT NULL,
            custom_week INTEGER NOT NULL,
            year        INTEGER NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS finance_settings (
            user_id        INTEGER PRIMARY KEY,
            weekly_budget  REAL DEFAULT 0,
            current_week   INTEGER DEFAULT 1,
            current_year   INTEGER DEFAULT 2026
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS budget_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            amount      REAL NOT NULL,
            week_from   INTEGER NOT NULL,
            year_from   INTEGER NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── check for existing data ──
    cur.execute("SELECT COUNT(*) FROM expenses WHERE user_id = ?", (args.user_id,))
    existing = cur.fetchone()[0]
    if existing > 0:
        print(f"WARNING: user {args.user_id} already has {existing} expense records.")
        answer = input("Continue and add duplicates? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            conn.close()
            sys.exit(0)

    # ── insert records ──
    inserted = 0
    for r in records:
        cur.execute(
            "INSERT INTO expenses (user_id, amount, custom_week, year, created_at) VALUES (?, ?, ?, ?, ?)",
            (args.user_id, r["amount"], r["custom_week"], r["year"], r["date"]),
        )
        inserted += 1

    # ── insert settings ──
    cur.execute(
        """INSERT INTO finance_settings (user_id, weekly_budget, current_week, current_year)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               weekly_budget=excluded.weekly_budget,
               current_week=excluded.current_week,
               current_year=excluded.current_year""",
        (args.user_id, weekly_budget, current_week, current_year),
    )

    conn.commit()
    conn.close()

    print(f"Done! Inserted {inserted} expense records.")
    print(f"Finance settings: budget={weekly_budget}, week={current_week}, year={current_year}")


if __name__ == "__main__":
    main()
