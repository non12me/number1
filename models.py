"""Modelos persistentes utilizados por la cola OCR."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class DocumentType(StrEnum):
    PEAJE = "PEAJE"
    BOLETA = "BOLETA"
    FACTURA = "FACTURA"


class JobState(StrEnum):
    SUBIENDO = "SUBIENDO"
    PENDIENTE = "PENDIENTE"
    PROCESANDO = "PROCESANDO"
    EXTRAIDO_LOCAL = "EXTRAIDO_LOCAL"
    NECESITA_REVISION = "NECESITA_REVISION"
    CONFIRMANDO = "CONFIRMANDO"
    CONFIRMADO = "CONFIRMADO"
    PENDIENTE_RESULTADO = "PENDIENTE_RESULTADO"
    ERROR = "ERROR"


class PdfMode(StrEnum):
    DOCUMENTO_UNICO = "DOCUMENTO_UNICO"
    PAGINAS_SEPARADAS = "PAGINAS_SEPARADAS"


def utc_now_iso() -> str:
    """Devuelve una fecha UTC estable para Google Sheets."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class QueueJob(BaseModel):
    """Representa exactamente una fila de OCR_COLA."""

    model_config = ConfigDict(use_enum_values=True)

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    sha256: str
    perceptual_hash: str = ""
    drive_file_id: str = ""
    nombre_original: str
    mime_type: str
    tipo_documento: DocumentType
    pagina_pdf: str = ""
    estado: JobState = JobState.SUBIENDO
    datos_extraidos_json: str = "{}"
    confianza_json: str = "{}"
    advertencias_json: str = "[]"
    error: str = ""
    usuario: str
    lock_owner: str = ""
    lock_until: str = ""
    intentos: int = 0
    gemini_usado: bool = False
    gemini_input_tokens: int = 0
    gemini_output_tokens: int = 0
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    confirmed_at: str = ""

    def as_sheet_record(self) -> dict[str, Any]:
        """Serializa valores seguros para escribirlos en Sheets."""
        record = self.model_dump(mode="json")
        record["gemini_usado"] = "TRUE" if self.gemini_usado else "FALSE"
        return record


class UploadResult(BaseModel):
    """Resultado resumido de la persistencia de un archivo."""

    status: str
    filename: str
    message: str
    jobs_created: int = 0


class QualityLevel(StrEnum):
    BUENA = "BUENA"
    ACEPTABLE = "ACEPTABLE"
    DEFICIENTE = "DEFICIENTE"
    ILEGIBLE = "ILEGIBLE"


class QualityReport(BaseModel):
    """Métricas objetivas calculadas sobre una página original."""

    width: int
    height: int
    blur_score: float
    brightness: float
    contrast: float
    rotation_degrees: float
    perspective_score: float
    document_area_ratio: float
    cut_border_score: float
    level: QualityLevel
    warnings: list[str] = Field(default_factory=list)


class OCRLine(BaseModel):
    """Una línea reconocida con trazabilidad de página y versión."""

    text: str
    confidence: float
    coordinates: list[list[float]]
    page: int
    preprocessing_version: str


class QRResult(BaseModel):
    """Resultado QR validado sin suponer valores faltantes."""

    detected: bool = False
    valid: bool = False
    raw_text: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    coordinates: list[list[float]] = Field(default_factory=list)
    source_image: str = "ORIGINAL"


class PageOCRResult(BaseModel):
    page: int
    quality: QualityReport
    qr: QRResult
    lines: list[OCRLine] = Field(default_factory=list)
    versions_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LocalOCRPayload(BaseModel):
    """Checkpoint completo de la Fase 4 para un documento lógico."""

    engine_profile: str
    pages: list[PageOCRResult]
    line_count: int
    mean_ocr_confidence: float
    weakest_quality: QualityLevel
    warnings: list[str] = Field(default_factory=list)
