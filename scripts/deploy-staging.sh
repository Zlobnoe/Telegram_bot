#!/usr/bin/env bash
# Usage: ./scripts/deploy-staging.sh [TAG]
#   TAG  — Docker image tag to deploy (default: latest)
#
# Examples:
#   ./scripts/deploy-staging.sh staging   # test a PR image
#   ./scripts/deploy-staging.sh latest    # test current prod image
#
# Must be run from the project root: ~/docker/telegram_bot

set -euo pipefail

STAGING_TAG="${1:-latest}"
REGISTRY="ghcr.io/zlobnoe/telegram_bot"

# Docker Compose project name == directory basename (how Compose derives it)
PROJECT=$(basename "$(pwd)")
PROD_VOL="${PROJECT}_bot-data"
STAGING_VOL="${PROJECT}_bot-staging-data"

echo "==> [1/5] Stopping staging container..."
docker compose -f docker-compose.staging.yml down

echo "==> [2/5] Copying prod DB to staging volume (${PROD_VOL} -> ${STAGING_VOL})..."
docker run --rm \
  -v "${PROD_VOL}:/source:ro" \
  -v "${STAGING_VOL}:/target" \
  alpine sh -c "cp /source/bot.db /target/bot.db && echo '  DB copied OK'"

echo "==> [3/5] Clearing pending reminders in staging DB..."
docker run --rm \
  -v "${STAGING_VOL}:/data" \
  "${REGISTRY}:${STAGING_TAG}" \
  python -c "
import sqlite3
conn = sqlite3.connect('/data/bot.db')
conn.execute('UPDATE reminders SET is_sent = 1 WHERE is_sent = 0')
conn.commit()
conn.close()
print('  Pending reminders cleared')
"

echo "==> [4/5] Pulling image ${REGISTRY}:${STAGING_TAG}..."
STAGING_TAG="${STAGING_TAG}" docker compose -f docker-compose.staging.yml pull

echo "==> [5/5] Starting staging..."
STAGING_TAG="${STAGING_TAG}" docker compose -f docker-compose.staging.yml up -d

echo ""
echo "Staging is running (tag=${STAGING_TAG})"
echo "Follow logs: docker compose -f docker-compose.staging.yml logs -f"
