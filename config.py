"""Configuración pública y no sensible del proyecto."""

from __future__ import annotations

APP_NAME = "OCR Documental Web"
APP_VERSION = "0.3.0"
PROJECT_PHASE = "Fase 3"
MOCK_MODE = False
GEMINI_ENABLED = False

GOOGLE_DATABASE_FILE_NAME = "OCR_DOCUMENTAL_DB"
GOOGLE_DATABASE_APP_PROPERTY_KEY = "ocr_role"
GOOGLE_DATABASE_APP_PROPERTY_VALUE = "database"

DRIVE_INPUT_FOLDER_NAME = "OCR_ENTRADA"
DRIVE_CONFIRMED_FOLDER_NAME = "OCR_CONFIRMADOS"
DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

MAX_FILES_PER_UPLOAD = 10
MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_PDF_PAGES = 200
VISUAL_DUPLICATE_MAX_DISTANCE = 6

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
}
