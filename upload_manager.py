"""Preparación segura de archivos antes de persistirlos."""

from __future__ import annotations

from dataclasses import dataclass

from models import DocumentType
from utils import (
    logical_page_labels,
    pdf_page_count,
    perceptual_hash_bytes,
    sha256_bytes,
    validate_upload,
)


@dataclass(frozen=True, slots=True)
class PreparedUpload:
    """Archivo validado que todavía vive solamente en memoria."""

    filename: str
    data: bytes
    mime_type: str
    document_type: DocumentType
    sha256: str
    perceptual_hash: str
    page_labels: tuple[str, ...]


def prepare_upload(
    filename: str,
    data: bytes,
    document_type: str,
    separate_pdf_pages: bool,
) -> PreparedUpload:
    """Valida y calcula metadatos sin modificar el original."""
    safe_name, mime_type = validate_upload(filename, data)
    sha256 = sha256_bytes(data)
    perceptual_hash = perceptual_hash_bytes(data, mime_type)

    if mime_type == "application/pdf":
        pages = pdf_page_count(data)
        page_labels = logical_page_labels(pages, separate_pdf_pages)
    else:
        page_labels = [""]

    return PreparedUpload(
        filename=safe_name,
        data=data,
        mime_type=mime_type,
        document_type=DocumentType(document_type),
        sha256=sha256,
        perceptual_hash=perceptual_hash,
        page_labels=tuple(page_labels),
    )
