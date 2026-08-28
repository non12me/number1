"""Carga temporal y preprocesamiento adaptativo sin modificar el original."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

from config import (
    OCR_LONG_RECEIPT_RATIO,
    OCR_SEGMENT_OVERLAP,
    OCR_TARGET_LONG_SIDE,
)
from quality import estimate_rotation, find_document_quad


@dataclass(frozen=True, slots=True)
class ImageSegment:
    image: np.ndarray
    y_offset: int


def _selected_pdf_pages(page_count: int, page_label: str) -> list[int]:
    if not page_label:
        return list(range(page_count))
    if "-" in page_label:
        start_text, end_text = page_label.split("-", maxsplit=1)
        start = max(1, int(start_text))
        end = min(page_count, int(end_text))
        return list(range(start - 1, end))
    page = int(page_label)
    if page < 1 or page > page_count:
        raise ValueError("La página indicada no existe en el PDF.")
    return [page - 1]


def iter_document_pages(
    file_path: str,
    mime_type: str,
    page_label: str,
) -> Iterator[tuple[int, np.ndarray]]:
    """Entrega una página a la vez para limitar el uso de memoria."""
    if mime_type == "application/pdf":
        with pymupdf.open(file_path) as document:
            for page_index in _selected_pdf_pages(document.page_count, page_label):
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
                rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                    pixmap.height,
                    pixmap.width,
                    pixmap.n,
                )
                if pixmap.n == 4:
                    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGBA2BGR)
                else:
                    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                yield page_index + 1, bgr
                del rgb, bgr, pixmap
        return

    with Image.open(file_path) as pil_image:
        corrected = ImageOps.exif_transpose(pil_image).convert("RGB")
        rgb = np.asarray(corrected)
        yield 1, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _rotate_bound(image: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.4:
        return image.copy()
    height, width = image.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cosine = abs(matrix[0, 0])
    sine = abs(matrix[0, 1])
    new_width = int((height * sine) + (width * cosine))
    new_height = int((height * cosine) + (width * sine))
    matrix[0, 2] += (new_width / 2.0) - center[0]
    matrix[1, 2] += (new_height / 2.0) - center[1]
    return cv2.warpAffine(
        image,
        matrix,
        (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _warp_document(image: np.ndarray, quad: np.ndarray | None) -> np.ndarray:
    if quad is None:
        return image
    top_left, top_right, bottom_right, bottom_left = quad
    width = int(
        max(
            np.linalg.norm(bottom_right - bottom_left),
            np.linalg.norm(top_right - top_left),
        )
    )
    height = int(
        max(
            np.linalg.norm(top_right - bottom_right),
            np.linalg.norm(top_left - bottom_left),
        )
    )
    if width < 100 or height < 100:
        return image
    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(quad.astype("float32"), destination)
    return cv2.warpPerspective(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _crop_quiet_border(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)[1]
    coordinates = cv2.findNonZero(mask)
    if coordinates is None:
        return image
    x, y, width, height = cv2.boundingRect(coordinates)
    image_area = image.shape[0] * image.shape[1]
    if width * height < image_area * 0.25:
        return image
    margin = max(4, int(min(image.shape[:2]) * 0.01))
    x1 = max(0, x - margin)
    y1 = max(0, y - margin)
    x2 = min(image.shape[1], x + width + margin)
    y2 = min(image.shape[0], y + height + margin)
    return image[y1:y2, x1:x2]


def _resize_for_ocr(image: np.ndarray, target: int = OCR_TARGET_LONG_SIDE) -> np.ndarray:
    height, width = image.shape[:2]
    long_side = max(height, width)
    if int(target * 0.90) <= long_side <= int(target * 1.10):
        return image
    scale = target / float(long_side)
    interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    return cv2.resize(
        image,
        (max(1, int(width * scale)), max(1, int(height * scale))),
        interpolation=interpolation,
    )


def preprocess_version_a(original: np.ndarray) -> np.ndarray:
    """Orientación, perspectiva, bordes y ajuste conservador en color."""
    quad = find_document_quad(original)
    corrected = _warp_document(original, quad)
    gray = cv2.cvtColor(corrected, cv2.COLOR_BGR2GRAY)
    corrected = _rotate_bound(corrected, estimate_rotation(gray))
    corrected = _crop_quiet_border(corrected)
    corrected = _resize_for_ocr(corrected)
    lab = cv2.cvtColor(corrected, cv2.COLOR_BGR2LAB)
    lightness, channel_a, channel_b = cv2.split(lab)
    lightness = cv2.createCLAHE(clipLimit=1.35, tileGridSize=(8, 8)).apply(lightness)
    return cv2.cvtColor(cv2.merge((lightness, channel_a, channel_b)), cv2.COLOR_LAB2BGR)


def preprocess_version_b(version_a: np.ndarray) -> np.ndarray:
    """Grises, CLAHE, ruido moderado y nitidez ligera."""
    gray = cv2.cvtColor(version_a, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    denoised = cv2.fastNlMeansDenoising(enhanced, None, h=7, templateWindowSize=7, searchWindowSize=21)
    blurred = cv2.GaussianBlur(denoised, (0, 0), 1.0)
    return cv2.addWeighted(denoised, 1.35, blurred, -0.35, 0)


def preprocess_version_c(version_a: np.ndarray) -> np.ndarray:
    """Corrección de iluminación, umbral adaptativo y morfología ligera."""
    gray = cv2.cvtColor(version_a, cv2.COLOR_BGR2GRAY)
    kernel_size = max(31, (min(gray.shape) // 12) | 1)
    background = cv2.morphologyEx(
        gray,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)),
    )
    normalized = cv2.divide(gray, np.maximum(background, 1), scale=255)
    binary = cv2.adaptiveThreshold(
        normalized,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35,
        11,
    )
    return cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
    )


def segment_long_receipt(image: np.ndarray) -> list[ImageSegment]:
    """Divide recibos largos con superposición sin cargar copias innecesarias."""
    height, width = image.shape[:2]
    if height / max(1, width) < OCR_LONG_RECEIPT_RATIO or height <= OCR_TARGET_LONG_SIDE:
        return [ImageSegment(image=image, y_offset=0)]
    segment_height = max(OCR_TARGET_LONG_SIDE, int(width * 2.2))
    overlap = min(OCR_SEGMENT_OVERLAP, segment_height // 4)
    segments: list[ImageSegment] = []
    start = 0
    while start < height:
        end = min(height, start + segment_height)
        segments.append(ImageSegment(image=image[start:end], y_offset=start))
        if end == height:
            break
        start = end - overlap
    return segments


def temporary_extension(filename: str, mime_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix:
        return suffix
    return ".pdf" if mime_type == "application/pdf" else ".img"

qr_reader.py

"""Lectura local de QR y validación conservadora de comprobantes peruanos."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import cv2
import numpy as np

from models import QRResult


def validate_peruvian_ruc(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    weights = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    total = sum(int(digits[index]) * weights[index] for index in range(10))
    check = 11 - (total % 11)
    if check == 10:
        check = 0
    elif check == 11:
        check = 1
    return check == int(digits[-1])


def _valid_date(value: str) -> str | None:
    for format_string in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(value.strip(), format_string)
        except ValueError:
            continue
        if 2000 <= parsed.year <= datetime.now().year + 1:
            return parsed.date().isoformat()
    return None


def _decimal(value: str) -> Decimal | None:
    text = re.sub(r"[^0-9,.-]", "", value.strip())
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_sunat_payload(raw_text: str) -> tuple[dict[str, str], list[str]]:
    """Interpreta el formato SUNAT separado por barras verticales."""
    warnings: list[str] = []
    parts = [part.strip() for part in raw_text.strip().split("|")]
    if len(parts) < 7:
        return {}, ["El QR no tiene el formato estructurado esperado."]

    ruc = re.sub(r"\D", "", parts[0])
    document_type = parts[1]
    series = parts[2].upper().replace(" ", "")
    number = re.sub(r"\s+", "", parts[3])
    igv = _decimal(parts[4])
    total = _decimal(parts[5])
    date = _valid_date(parts[6])

    if not validate_peruvian_ruc(ruc):
        warnings.append("El RUC del QR no supera el dígito verificador.")
    if not re.fullmatch(r"[A-Z0-9]{1,6}", series) or not re.fullmatch(r"\d{1,12}", number):
        warnings.append("La serie o el número del QR no tiene formato válido.")
    if date is None:
        warnings.append("La fecha del QR no es válida.")
    if total is None or total <= 0:
        warnings.append("El total del QR no es válido.")
    if igv is None or igv < 0:
        warnings.append("El IGV del QR no es válido.")
    if total is not None and igv is not None and igv > total:
        warnings.append("El IGV del QR es mayor que el total.")

    if warnings:
        return {}, warnings

    fields = {
        "ruc_emisor": ruc,
        "tipo_comprobante_codigo": document_type,
        "serie_numero": f"{series}-{number}",
        "igv": format(igv, "f"),
        "monto_total": format(total, "f"),
        "fecha": date or "",
    }
    if len(parts) >= 9:
        client_document = re.sub(r"\D", "", parts[8])
        if client_document:
            fields["documento_cliente"] = client_document
    return fields, []


def _points_to_list(points: np.ndarray | None) -> list[list[float]]:
    if points is None:
        return []
    array = np.asarray(points, dtype=float).reshape(-1, 2)
    return [[round(float(x), 2), round(float(y), 2)] for x, y in array]


def _decode_candidates(image: np.ndarray) -> list[tuple[str, list[list[float]]]]:
    detector = cv2.QRCodeDetector()
    candidates: list[tuple[str, list[list[float]]]] = []
    try:
        found, decoded, points, _ = detector.detectAndDecodeMulti(image)
        if found and decoded:
            point_sets = np.asarray(points) if points is not None else []
            for index, text in enumerate(decoded):
                if text:
                    candidate_points = point_sets[index] if len(point_sets) > index else None
                    candidates.append((str(text), _points_to_list(candidate_points)))
    except (cv2.error, ValueError):
        pass
    if candidates:
        return candidates
    try:
        text, points, _ = detector.detectAndDecode(image)
        if text:
            candidates.append((str(text), _points_to_list(points)))
    except cv2.error:
        pass
    return candidates


def read_qr(image: np.ndarray, source_image: str = "ORIGINAL") -> QRResult:
    """Detecta QR y solo acepta campos que superan todas las validaciones."""
    candidates = _decode_candidates(image)
    if not candidates:
        return QRResult(source_image=source_image)

    collected_warnings: list[str] = []
    for raw_text, coordinates in candidates:
        fields, warnings = parse_sunat_payload(raw_text)
        if fields:
            return QRResult(
                detected=True,
                valid=True,
                raw_text=raw_text,
                fields=fields,
                warnings=[],
                coordinates=coordinates,
                source_image=source_image,
            )
        collected_warnings.extend(warnings)
    raw_text, coordinates = candidates[0]
    return QRResult(
        detected=True,
        valid=False,
        raw_text=raw_text,
        fields={},
        warnings=list(dict.fromkeys(collected_warnings)),
        coordinates=coordinates,
        source_image=source_image,
    )
