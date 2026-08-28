"""Operaciones mínimas de Google Sheets para la Fase 2."""

from __future__ import annotations

from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from google_auth import GoogleConfigurationError


def build_sheets_service(credentials: Credentials) -> Resource:
    """Construye el cliente de Google Sheets."""
    return build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )


def test_sheets_connection(
    credentials: Credentials,
    spreadsheet_id: str,
) -> dict[str, str]:
    """Comprueba acceso a la hoja configurada sin leer celdas."""
    if not spreadsheet_id:
        raise GoogleConfigurationError("Falta GOOGLE_SHEET_ID")

    service = build_sheets_service(credentials)
    response: dict[str, Any] = (
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            includeGridData=False,
            fields="properties(title,locale,timeZone)",
        )
        .execute()
    )
    properties = response.get("properties", {})
    return {
        "title": str(properties.get("title", "")),
        "locale": str(properties.get("locale", "")),
        "timeZone": str(properties.get("timeZone", "")),
    }
