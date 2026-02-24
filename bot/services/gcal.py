from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GCalService:
    def __init__(self, credentials_path: str, calendar_id: str, timezone: str = "UTC") -> None:
        self.calendar_id = calendar_id
        self.timezone = timezone
        creds = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=SCOPES,
        )
        self._service = build("calendar", "v3", credentials=creds)

    async def get_events(
        self, date_from: datetime, date_to: datetime,
    ) -> list[dict]:
        def _fetch() -> list[dict]:
            result = (
                self._service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=date_from.isoformat(),
                    timeMax=date_to.isoformat(),
                    timeZone=self.timezone,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=50,
                )
                .execute()
            )
            return result.get("items", [])

        return await asyncio.to_thread(_fetch)

    async def create_event(
        self, summary: str, start: datetime, end: datetime,
    ) -> dict:
        def _create() -> dict:
            body = {
                "summary": summary,
                "start": {"dateTime": start.isoformat(), "timeZone": self.timezone},
                "end": {"dateTime": end.isoformat(), "timeZone": self.timezone},
            }
            return (
                self._service.events()
                .insert(calendarId=self.calendar_id, body=body)
                .execute()
            )

        return await asyncio.to_thread(_create)

    async def delete_event(self, event_id: str) -> bool:
        def _delete() -> bool:
            try:
                self._service.events().delete(
                    calendarId=self.calendar_id, eventId=event_id,
                ).execute()
                return True
            except Exception:
                logger.exception("Failed to delete event %s", event_id)
                return False

        return await asyncio.to_thread(_delete)


def create_gcal_service(
    credentials_path: str | None, calendar_id: str | None, timezone: str = "UTC",
) -> GCalService | None:
    if not credentials_path or not calendar_id:
        return None
    if not os.path.exists(credentials_path):
        logger.warning("Google credentials file not found: %s", credentials_path)
        return None
    try:
        return GCalService(credentials_path, calendar_id, timezone)
    except Exception:
        logger.exception("Failed to init GCalService")
        return None


class GCalRegistry:
    """Factory/cache of GCalService instances keyed by calendar_id.

    Uses a single service account credentials file for all calendars.
    Users must share their Google Calendar with the service account email.
    """

    def __init__(self, credentials_path: str, timezone: str = "UTC") -> None:
        self._credentials_path = credentials_path
        self._timezone = timezone
        self._cache: dict[str, GCalService] = {}

    def get_service(self, calendar_id: str) -> GCalService:
        if calendar_id not in self._cache:
            self._cache[calendar_id] = GCalService(
                self._credentials_path, calendar_id, self._timezone
            )
        return self._cache[calendar_id]

    @property
    def service_account_email(self) -> str | None:
        """Return the service account email from the credentials file."""
        try:
            import json
            with open(self._credentials_path) as f:
                data = json.load(f)
            return data.get("client_email")
        except Exception:
            return None


def create_gcal_registry(
    credentials_path: str | None, timezone: str = "UTC",
) -> "GCalRegistry | None":
    if not credentials_path:
        return None
    if not os.path.exists(credentials_path):
        logger.warning("Google credentials file not found: %s", credentials_path)
        return None
    try:
        # Validate credentials are loadable
        service_account.Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        return GCalRegistry(credentials_path, timezone)
    except Exception:
        logger.exception("Failed to init GCalRegistry")
        return None
