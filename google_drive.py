"""Operaciones mínimas de Google Drive para la Fase 2."""

from __future__ import annotations

from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from config import (
    GOOGLE_DATABASE_APP_PROPERTY_KEY,
    GOOGLE_DATABASE_APP_PROPERTY_VALUE,
    GOOGLE_DATABASE_FILE_NAME,
)


SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"


def build_drive_service(credentials: Credentials) -> Resource:
    """Construye el cliente de Drive sin caché local de descubrimiento."""
    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


def test_drive_connection(credentials: Credentials) -> dict[str, str]:
    """Comprueba la autorización sin listar contenido privado del usuario."""
    service = build_drive_service(credentials)
    response: dict[str, Any] = (
        service.about()
        .get(fields="user(displayName,emailAddress)")
        .execute()
    )
    user = response.get("user", {})
    return {
        "display_name": str(user.get("displayName", "")),
        "email": str(user.get("emailAddress", "")),
    }


def ensure_database_spreadsheet(credentials: Credentials) -> bool:
    """Crea la hoja base una sola vez y la recupera por appProperties.

    Returns:
        True cuando crea el archivo y False cuando ya existía.
    """
    service = build_drive_service(credentials)
    query = (
        "trashed = false and "
        f"mimeType = '{SPREADSHEET_MIME_TYPE}' and "
        "appProperties has { "
        f"key='{GOOGLE_DATABASE_APP_PROPERTY_KEY}' and "
        f"value='{GOOGLE_DATABASE_APP_PROPERTY_VALUE}' "
        "}"
    )

    response = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            pageSize=1,
            fields="files(id,name)",
        )
        .execute()
    )
    if response.get("files"):
        return False

    metadata = {
        "name": GOOGLE_DATABASE_FILE_NAME,
        "mimeType": SPREADSHEET_MIME_TYPE,
        "appProperties": {
            GOOGLE_DATABASE_APP_PROPERTY_KEY: GOOGLE_DATABASE_APP_PROPERTY_VALUE,
        },
    }
    service.files().create(body=metadata, fields="id").execute()
    return True
