"""Funciones puras para archivos, hashes y validación básica."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageOps

from config import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
    MAX_PDF_PAGES,
    SHEET_JSON_PLAIN_LIMIT,
)


MIME_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}


def sha256_bytes(data: bytes) -> str:
    """Calcula el SHA-256 exacto del archivo original."""
    return hashlib.sha256(data).hexdigest()


def sanitize_filename(filename: str, max_length: int = 160) -> str:
    """Limpia el nombre conservando una extensión permitida."""
    original = Path(filename).name
    suffix = Path(original).suffix.lower()
    stem = Path(original).stem
    normalized = unicodedata.normalize("NFKC", stem)
    normalized = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .-")
    if not normalized:
        normalized = "documento"
    room = max(1, max_length - len(suffix))
    return f"{normalized[:room]}{suffix}"


def detect_mime_type(data: bytes) -> str:
    """Detecta formatos permitidos mediante su firma binaria."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("El contenido no corresponde a JPG, PNG, WEBP o PDF.")


def validate_upload(filename: str, data: bytes) -> tuple[str, str]:
    """Valida tamaño, extensión y firma, y devuelve nombre y MIME seguros."""
    if not data:
        raise ValueError("El archivo está vacío.")
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise ValueError("El archivo supera el límite de 20 MB.")

    safe_name = sanitize_filename(filename)
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Extensión no permitida.")

    detected_mime = detect_mime_type(data)
    if detected_mime not in ALLOWED_MIME_TYPES:
        raise ValueError("Tipo MIME no permitido.")
    if MIME_BY_EXTENSION[extension] != detected_mime:
        raise ValueError("La extensión no coincide con el contenido real.")
    return safe_name, detected_mime


def perceptual_hash_bytes(data: bytes, mime_type: str) -> str:
    """Calcula un dHash de 64 bits para imágenes; PDF devuelve vacío."""
    if not mime_type.startswith("image/"):
        return ""
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image).convert("L")
        image = image.resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(image.get_flattened_data())

    bits = []
    for row in range(8):
        offset = row * 9
        for column in range(8):
            bits.append(pixels[offset + column] > pixels[offset + column + 1])

    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming_distance_hex(left: str, right: str) -> int:
    """Calcula distancia de Hamming entre dos hashes hexadecimales."""
    if not left or not right or len(left) != len(right):
        return 10_000
    return (int(left, 16) ^ int(right, 16)).bit_count()


def pdf_page_count(data: bytes) -> int:
    """Cuenta páginas de un PDF sin crear archivos locales."""
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            pages = document.page_count
    except Exception as exc:  # noqa: BLE001
        raise ValueError("El PDF está dañado o protegido y no puede abrirse.") from exc

    if pages < 1:
        raise ValueError("El PDF no contiene páginas.")
    if pages > MAX_PDF_PAGES:
        raise ValueError(f"El PDF supera el límite de {MAX_PDF_PAGES} páginas.")
    return pages


def logical_page_labels(page_count: int, separate_pages: bool) -> list[str]:
    """Crea las referencias lógicas sin duplicar el PDF original."""
    if page_count <= 1:
        return ["1"]
    if separate_pages:
        return [str(page) for page in range(1, page_count + 1)]
    return [f"1-{page_count}"]


def encode_json_for_sheet(value: Any) -> str:
    """Comprime JSON grande para respetar el límite por celda de Sheets."""
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(raw) <= SHEET_JSON_PLAIN_LIMIT:
        return raw
    compressed = gzip.compress(raw.encode("utf-8"), compresslevel=6)
    return "GZIP64:" + base64.b64encode(compressed).decode("ascii")


def decode_json_from_sheet(value: str) -> Any:
    """Lee indistintamente JSON normal o el formato comprimido de la app."""
    if not value:
        return {}
    if value.startswith("GZIP64:"):
        compressed = base64.b64decode(value.removeprefix("GZIP64:"))
        value = gzip.decompress(compressed).decode("utf-8")
    return json.loads(value)
