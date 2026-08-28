"""Persistencia estructurada en Google Sheets."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from google_auth import GoogleConfigurationError


OCR_QUEUE_HEADERS = [
    "job_id",
    "sha256",
    "perceptual_hash",
    "drive_file_id",
    "nombre_original",
    "mime_type",
    "tipo_documento",
    "pagina_pdf",
    "estado",
    "datos_extraidos_json",
    "confianza_json",
    "advertencias_json",
    "error",
    "usuario",
    "lock_owner",
    "lock_until",
    "intentos",
    "gemini_usado",
    "gemini_input_tokens",
    "gemini_output_tokens",
    "created_at",
    "updated_at",
    "confirmed_at",
]

SHEET_HEADERS = {
    "OCR_COLA": OCR_QUEUE_HEADERS,
    "PEAJES": [
        "numero", "job_id", "concesionaria", "lugar", "estacion", "fecha",
        "hora", "placa", "serie_numero", "subtotal", "igv", "monto_total",
        "moneda", "nombre_archivo", "drive_file_id", "usuario", "confirmed_at",
    ],
    "BOLETAS": [
        "numero", "job_id", "ruc_emisor", "razon_social", "serie_numero",
        "fecha", "documento_cliente", "concepto_resumen", "items_json",
        "subtotal", "igv", "monto_total", "moneda", "nombre_archivo",
        "drive_file_id", "usuario", "confirmed_at",
    ],
    "FACTURAS": [
        "numero", "job_id", "ruc_emisor", "razon_social", "serie_numero",
        "fecha", "documento_cliente", "concepto_resumen", "items_json",
        "subtotal", "igv", "monto_total", "moneda", "nombre_archivo",
        "drive_file_id", "usuario", "confirmed_at",
    ],
    "DICCIONARIOS": [
        "tipo", "valor_canonico", "variante", "proveedor", "activo",
        "created_at", "created_by",
    ],
    "PLANTILLAS": [
        "proveedor", "palabras_identificadoras", "posicion_ruc", "posicion_fecha",
        "posicion_placa", "posicion_total", "etiquetas_utilizadas",
        "formato_fecha", "activo",
    ],
    "CORRECCIONES": [
        "job_id", "tipo_documento", "proveedor", "campo", "texto_ocr_original",
        "valor_detectado", "valor_corregido", "usuario", "fecha",
    ],
    "LOGS": [
        "log_id", "accion", "job_id", "sha256", "detalle", "usuario", "created_at",
    ],
    "CONFIGURACION": [
        "clave", "valor", "tipo", "descripcion", "updated_at", "updated_by",
    ],
}

DEFAULT_CONFIG = [
    ["ocr_lado_largo", "2000", "int", "Resolución objetivo OCR", "", "SISTEMA"],
    ["ocr_perfil", "PP-OCRv6-TINY", "text", "Perfil OCR CPU", "", "SISTEMA"],
    ["ocr_cpu_threads", "2", "int", "Hilos máximos del OCR", "", "SISTEMA"],
    ["ocr_min_score", "0.35", "decimal", "Línea OCR mínima conservada", "", "SISTEMA"],
    ["ocr_recovery_score", "0.75", "decimal", "Umbral para probar B o C", "", "SISTEMA"],
    ["ocr_lock_minutes", "30", "int", "Duración del lease por job", "", "SISTEMA"],
    ["peso_ocr", "0.35", "decimal", "Peso de confianza OCR", "", "SISTEMA"],
    ["peso_formato", "0.25", "decimal", "Peso de formato válido", "", "SISTEMA"],
    ["peso_etiqueta", "0.20", "decimal", "Peso de etiqueta cercana", "", "SISTEMA"],
    ["peso_posicion", "0.10", "decimal", "Peso de posición", "", "SISTEMA"],
    ["peso_consistencia", "0.10", "decimal", "Peso de consistencia", "", "SISTEMA"],
    ["umbral_confiable", "0.90", "decimal", "Dato confiable", "", "SISTEMA"],
    ["umbral_revision", "0.75", "decimal", "Dato que requiere recuperación", "", "SISTEMA"],
    ["tolerancia_monetaria", "0.02", "decimal", "Tolerancia de importes", "", "SISTEMA"],
    ["gemini_enabled", "false", "bool", "Interruptor de Gemini", "", "SISTEMA"],
]


def build_sheets_service(credentials: Credentials) -> Resource:
    """Construye el cliente Sheets v4."""
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _quote_sheet(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def _column_letter(number: int) -> str:
    result = ""
    value = number
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def test_sheets_connection(
    credentials: Credentials,
    spreadsheet_id: str,
) -> dict[str, str]:
    """Comprueba acceso sin leer celdas."""
    if not spreadsheet_id:
        raise GoogleConfigurationError("Falta GOOGLE_SHEET_ID")
    response = build_sheets_service(credentials).spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        includeGridData=False,
        fields="properties(title,locale,timeZone)",
    ).execute()
    properties = response.get("properties", {})
    return {
        "title": str(properties.get("title", "")),
        "locale": str(properties.get("locale", "")),
        "timeZone": str(properties.get("timeZone", "")),
    }


def ensure_workbook_structure(
    credentials: Credentials,
    spreadsheet_id: str,
) -> dict[str, int]:
    """Crea pestañas y encabezados de forma idempotente."""
    service = build_sheets_service(credentials)
    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        includeGridData=False,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    sheets = metadata.get("sheets", [])
    existing = {
        str(item["properties"]["title"]): int(item["properties"]["sheetId"])
        for item in sheets
    }

    requests: list[dict[str, Any]] = []
    if "OCR_COLA" not in existing and len(existing) == 1 and "Sheet1" in existing:
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": existing["Sheet1"],
                        "title": "OCR_COLA",
                    },
                    "fields": "title",
                }
            }
        )
        existing["OCR_COLA"] = existing.pop("Sheet1")

    for title in SHEET_HEADERS:
        if title not in existing:
            requests.append({"addSheet": {"properties": {"title": title}}})

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()

    header_data = [
        {
            "range": f"{_quote_sheet(title)}!A1",
            "values": [headers],
        }
        for title, headers in SHEET_HEADERS.items()
    ]
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "RAW", "data": header_data},
    ).execute()

    config_values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="'CONFIGURACION'!A2:A",
    ).execute().get("values", [])
    existing_keys = {str(row[0]) for row in config_values if row}
    missing_config = [row for row in DEFAULT_CONFIG if row[0] not in existing_keys]
    if missing_config:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range="'CONFIGURACION'!A2",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": missing_config},
        ).execute()

    return {"sheet_count": len(SHEET_HEADERS), "header_count": len(SHEET_HEADERS)}


def read_records(
    credentials: Credentials,
    spreadsheet_id: str,
    sheet_name: str,
) -> list[dict[str, str]]:
    """Lee una pestaña y la convierte a registros por encabezado."""
    if sheet_name not in SHEET_HEADERS:
        raise ValueError("Pestaña no permitida.")
    headers = SHEET_HEADERS[sheet_name]
    values = build_sheets_service(credentials).spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{_quote_sheet(sheet_name)}!A:{_column_letter(len(headers))}",
    ).execute().get("values", [])
    if len(values) <= 1:
        return []

    records: list[dict[str, str]] = []
    for row in values[1:]:
        padded = list(row) + [""] * (len(headers) - len(row))
        records.append({header: str(padded[index]) for index, header in enumerate(headers)})
    return records


def append_records(
    credentials: Credentials,
    spreadsheet_id: str,
    sheet_name: str,
    records: Iterable[dict[str, Any]],
) -> int:
    """Añade filas completas según el orden canónico de encabezados."""
    if sheet_name not in SHEET_HEADERS:
        raise ValueError("Pestaña no permitida.")
    headers = SHEET_HEADERS[sheet_name]
    rows = [[record.get(header, "") for header in headers] for record in records]
    if not rows:
        return 0
    build_sheets_service(credentials).spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{_quote_sheet(sheet_name)}!A2",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    return len(rows)


def update_queue_jobs(
    credentials: Credentials,
    spreadsheet_id: str,
    updates: dict[str, dict[str, Any]],
) -> int:
    """Relee OCR_COLA y actualiza filas completas por job_id."""
    if not updates:
        return 0
    service = build_sheets_service(credentials)
    headers = OCR_QUEUE_HEADERS
    values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'OCR_COLA'!A:{_column_letter(len(headers))}",
    ).execute().get("values", [])
    data = []
    for sheet_row, row in enumerate(values[1:], start=2):
        current = list(row) + [""] * (len(headers) - len(row))
        job_id = str(current[0])
        if job_id not in updates:
            continue
        merged = {headers[index]: current[index] for index in range(len(headers))}
        merged.update(updates[job_id])
        data.append(
            {
                "range": f"'OCR_COLA'!A{sheet_row}:{_column_letter(len(headers))}{sheet_row}",
                "values": [[merged.get(header, "") for header in headers]],
            }
        )
    if not data:
        return 0
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "RAW", "data": data},
    ).execute()
    return len(data)
