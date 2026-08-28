"""Procesamiento completo de un job usando una copia temporal de Drive."""

from __future__ import annotations

import tempfile
from pathlib import Path

from google.oauth2.credentials import Credentials

from google_drive import download_file_to_path
from local_ocr import run_adaptive_ocr
from models import (
    JobState,
    LocalOCRPayload,
    PageOCRResult,
    QualityLevel,
    QRResult,
)
from preprocessing import (
    iter_document_pages,
    preprocess_version_a,
    temporary_extension,
)
from qr_reader import read_qr
from quality import assess_image_quality


QUALITY_RANK = {
    QualityLevel.BUENA: 0,
    QualityLevel.ACEPTABLE: 1,
    QualityLevel.DEFICIENTE: 2,
    QualityLevel.ILEGIBLE: 3,
}


def _best_qr(original_qr: QRResult, corrected_qr: QRResult) -> QRResult:
    if original_qr.valid:
        return original_qr
    if corrected_qr.valid:
        return corrected_qr
    if original_qr.detected:
        return original_qr
    return corrected_qr


def process_document_job(
    credentials: Credentials,
    job: dict[str, str],
) -> tuple[LocalOCRPayload, JobState]:
    """Descarga, procesa página a página y borra únicamente la copia temporal."""
    filename = job.get("nombre_original", "documento")
    mime_type = job.get("mime_type", "")
    suffix = temporary_extension(filename, mime_type)
    page_results: list[PageOCRResult] = []
    document_warnings: list[str] = []
    engine_profile = "NO_CARGADO"

    with tempfile.TemporaryDirectory(prefix="ocr_job_") as temporary_directory:
        temporary_path = str(Path(temporary_directory) / f"original{suffix}")
        download_file_to_path(
            credentials,
            job.get("drive_file_id", ""),
            temporary_path,
        )
        for page_number, original in iter_document_pages(
            temporary_path,
            mime_type,
            job.get("pagina_pdf", ""),
        ):
            quality = assess_image_quality(original)
            warnings = list(quality.warnings)
            corrected = preprocess_version_a(original)
            qr_original = read_qr(original, "ORIGINAL")
            qr_corrected = read_qr(corrected, "PERSPECTIVA_CORREGIDA")
            qr = _best_qr(qr_original, qr_corrected)
            if qr.detected and not qr.valid:
                warnings.extend(qr.warnings)

            if quality.level == QualityLevel.ILEGIBLE:
                warnings.append(
                    "Página ilegible: se requiere una nueva fotografía; OCR omitido."
                )
                page_results.append(
                    PageOCRResult(
                        page=page_number,
                        quality=quality,
                        qr=qr,
                        lines=[],
                        versions_used=[],
                        warnings=list(dict.fromkeys(warnings)),
                    )
                )
                del original, corrected
                continue

            profile, lines, versions, _ = run_adaptive_ocr(
                original,
                page_number,
                quality.level,
            )
            engine_profile = profile
            if not lines:
                warnings.append("PaddleOCR no encontró texto utilizable en esta página.")
            page_results.append(
                PageOCRResult(
                    page=page_number,
                    quality=quality,
                    qr=qr,
                    lines=lines,
                    versions_used=versions,
                    warnings=list(dict.fromkeys(warnings)),
                )
            )
            del original, corrected, lines

    if not page_results:
        raise ValueError("El documento no produjo páginas procesables.")

    all_lines = [line for page in page_results for line in page.lines]
    mean_confidence = (
        sum(line.confidence for line in all_lines) / len(all_lines)
        if all_lines
        else 0.0
    )
    weakest = max(
        (page.quality.level for page in page_results),
        key=lambda level: QUALITY_RANK[level],
    )
    for page in page_results:
        document_warnings.extend(page.warnings)
    payload = LocalOCRPayload(
        engine_profile=engine_profile,
        pages=page_results,
        line_count=len(all_lines),
        mean_ocr_confidence=round(mean_confidence, 4),
        weakest_quality=weakest,
        warnings=list(dict.fromkeys(document_warnings)),
    )
    if weakest == QualityLevel.ILEGIBLE or not all_lines:
        return payload, JobState.NECESITA_REVISION
    return payload, JobState.EXTRAIDO_LOCAL
