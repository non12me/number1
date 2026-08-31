"""Acceso limitado a carpetas y archivos creados por la aplicación."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from config import (
    DRIVE_CONFIRMED_FOLDER_NAME,
    DRIVE_FOLDER_MIME_TYPE,
    DRIVE_INPUT_FOLDER_NAME,
    GOOGLE_DATABASE_APP_PROPERTY_KEY,
    GOOGLE_DATABASE_APP_PROPERTY_VALUE,
    GOOGLE_DATABASE_FILE_NAME,
)


SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"


def build_drive_service(credentials: Credentials) -> Resource:
    """Construye el cliente Drive v3 sin caché local."""
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def test_drive_connection(credentials: Credentials) -> dict[str, str]:
    """Comprueba autorización sin listar contenido privado."""
    service = build_drive_service(credentials)
    response: dict[str, Any] = (
        service.about().get(fields="user(displayName,emailAddress)").execute()
    )
    user = response.get("user", {})
    return {
        "display_name": str(user.get("displayName", "")),
        "email": str(user.get("emailAddress", "")),
    }


def ensure_database_spreadsheet(credentials: Credentials) -> bool:
    """Crea la hoja base una sola vez; se conserva por compatibilidad."""
    service = build_drive_service(credentials)
    query = (
        "trashed = false and "
        f"mimeType = '{SPREADSHEET_MIME_TYPE}' and "
        "appProperties has { "
        f"key='{GOOGLE_DATABASE_APP_PROPERTY_KEY}' and "
        f"value='{GOOGLE_DATABASE_APP_PROPERTY_VALUE}' "
        "}"
    )
    response = service.files().list(
        q=query,
        spaces="drive",
        pageSize=1,
        fields="files(id,name)",
    ).execute()
    if response.get("files"):
        return False

    service.files().create(
        body={
            "name": GOOGLE_DATABASE_FILE_NAME,
            "mimeType": SPREADSHEET_MIME_TYPE,
            "appProperties": {
                GOOGLE_DATABASE_APP_PROPERTY_KEY: GOOGLE_DATABASE_APP_PROPERTY_VALUE,
            },
        },
        fields="id",
    ).execute()
    return True


def _ensure_folder(
    service: Resource,
    folder_name: str,
    role: str,
) -> tuple[str, bool]:
    """Busca o crea una carpeta identificada por una propiedad privada."""
    query = (
        "trashed = false and "
        f"mimeType = '{DRIVE_FOLDER_MIME_TYPE}' and "
        "appProperties has { key='ocr_role' and "
        f"value='{role}' }}"
    )
    response = service.files().list(
        q=query,
        spaces="drive",
        pageSize=1,
        fields="files(id,name)",
    ).execute()
    files = response.get("files", [])
    if files:
        return str(files[0]["id"]), False

    folder = service.files().create(
        body={
            "name": folder_name,
            "mimeType": DRIVE_FOLDER_MIME_TYPE,
            "appProperties": {"ocr_role": role},
        },
        fields="id",
    ).execute()
    return str(folder["id"]), True


def ensure_storage_folders(credentials: Credentials) -> dict[str, bool]:
    """Crea o recupera exactamente las dos carpetas principales."""
    service = build_drive_service(credentials)
    _, input_created = _ensure_folder(service, DRIVE_INPUT_FOLDER_NAME, "input")
    _, confirmed_created = _ensure_folder(
        service,
        DRIVE_CONFIRMED_FOLDER_NAME,
        "confirmed",
    )
    return {
        "input_created": input_created,
        "confirmed_created": confirmed_created,
    }


def validate_folder(
    credentials: Credentials,
    folder_id: str,
    expected_name: str,
) -> dict[str, str]:
    """Comprueba que un ID corresponda a la carpeta esperada."""
    if not folder_id:
        raise ValueError(f"Falta el ID de {expected_name}.")
    service = build_drive_service(credentials)
    metadata = service.files().get(
        fileId=folder_id,
        fields="id,name,mimeType,trashed",
    ).execute()
    if metadata.get("trashed"):
        raise ValueError(f"La carpeta {expected_name} está en la papelera.")
    if metadata.get("mimeType") != DRIVE_FOLDER_MIME_TYPE:
        raise ValueError(f"El ID configurado para {expected_name} no es una carpeta.")
    if metadata.get("name") != expected_name:
        raise ValueError(f"El ID no corresponde a la carpeta {expected_name}.")
    return {"id": str(metadata["id"]), "name": str(metadata["name"])}


def find_file_by_sha(
    credentials: Credentials,
    folder_id: str,
    sha256: str,
) -> str:
    """Recupera un archivo subido cuyo checkpoint de Sheets quedó incompleto."""
    service = build_drive_service(credentials)
    query = (
        "trashed = false and "
        f"'{folder_id}' in parents and "
        "appProperties has { key='sha256' and "
        f"value='{sha256}' }}"
    )
    response = service.files().list(
        q=query,
        spaces="drive",
        pageSize=1,
        fields="files(id)",
    ).execute()
    files = response.get("files", [])
    return str(files[0]["id"]) if files else ""


def upload_original_bytes(
    credentials: Credentials,
    folder_id: str,
    filename: str,
    mime_type: str,
    data: bytes,
    sha256: str,
    progress_callback: Callable[[float], None] | None = None,
) -> str:
    """Sube el original una sola vez mediante carga reanudable."""
    service = build_drive_service(credentials)
    media = MediaIoBaseUpload(
        io.BytesIO(data),
        mimetype=mime_type,
        chunksize=1024 * 1024,
        resumable=True,
    )
    request = service.files().create(
        body={
            "name": filename,
            "parents": [folder_id],
            "appProperties": {
                "sha256": sha256,
                "ocr_status": "input",
            },
        },
        media_body=media,
        fields="id",
    )

    response = None
    while response is None:
        status, response = request.next_chunk(num_retries=2)
        if status is not None and progress_callback is not None:
            progress_callback(float(status.progress()))

    if progress_callback is not None:
        progress_callback(1.0)
    return str(response["id"])


def download_file_to_path(
    credentials: Credentials,
    file_id: str,
    destination: str,
) -> None:
    """Descarga una copia temporal del original sin alterar Drive."""
    if not file_id:
        raise ValueError("El job no tiene drive_file_id.")
    service = build_drive_service(credentials)
    request = service.files().get_media(fileId=file_id)
    with open(destination, "wb") as output:
        downloader = MediaIoBaseDownload(output, request, chunksize=1024 * 1024)
        completed = False
        while not completed:
            _, completed = downloader.next_chunk(num_retries=2)


def download_file_bytes(
    credentials: Credentials,
    file_id: str,
) -> bytes:
    """Descarga un original en memoria para la vista previa de revisión."""
    if not file_id:
        raise ValueError("El job no tiene drive_file_id.")
    request = build_drive_service(credentials).files().get_media(fileId=file_id)
    output = io.BytesIO()
    downloader = MediaIoBaseDownload(output, request, chunksize=1024 * 1024)
    completed = False
    while not completed:
        _, completed = downloader.next_chunk(num_retries=2)
    return output.getvalue()


def move_file_to_folder(
    credentials: Credentials,
    file_id: str,
    destination_folder_id: str,
    new_name: str,
) -> dict[str, str]:
    """Mueve el mismo archivo, sin copiarlo, y permite reintentos seguros."""
    if not file_id or not destination_folder_id:
        raise ValueError("Falta el archivo o la carpeta de confirmados.")
    service = build_drive_service(credentials)
    current = service.files().get(
        fileId=file_id,
        fields="id,name,parents,trashed,appProperties",
    ).execute()
    if current.get("trashed"):
        raise ValueError("El original está en la papelera.")
    parents = [str(parent) for parent in current.get("parents", [])]
    app_properties = dict(current.get("appProperties") or {})
    app_properties["ocr_status"] = "confirmed"
    request_arguments: dict[str, Any] = {
        "fileId": file_id,
        "body": {"name": new_name, "appProperties": app_properties},
        "fields": "id,name,parents",
    }
    if destination_folder_id not in parents:
        request_arguments["addParents"] = destination_folder_id
        removable = [parent for parent in parents if parent != destination_folder_id]
        if removable:
            request_arguments["removeParents"] = ",".join(removable)
    updated = service.files().update(**request_arguments).execute()
    return {
        "id": str(updated.get("id", file_id)),
        "name": str(updated.get("name", new_name)),
        "parents": ",".join(str(item) for item in updated.get("parents", [])),
    }
