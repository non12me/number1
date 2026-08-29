"""Generación exhaustiva de candidatos para campos de peaje."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal

from rapidfuzz import fuzz

from confidence import score_candidate
from models import FieldCandidate, OCRLine, PageOCRResult, ValueSource
from validators import (
    DATE_PATTERN,
    SERIES_PATTERN,
    TIME_PATTERN,
    currency_from_text,
    decimal_text,
    normalize_date,
    normalize_decimal,
    normalize_plate,
    normalize_series_number,
    normalize_time,
)


FIELD_LABELS = {
    "concesionaria": ("CONCESIONARIA", "EMPRESA", "RAZON SOCIAL"),
    "lugar": ("LUGAR", "UBICACION", "LOCALIDAD", "CIUDAD"),
    "estacion": ("ESTACION", "PLAZA", "UNIDAD DE PEAJE", "PEAJE"),
    "fecha": ("FECHA EMISION", "FECHA DE EMISION", "FECHA", "TRANSITO"),
    "hora": ("HORA", "TRANSITO"),
    "placa": ("PLACA", "PATENTE", "MATRICULA"),
    "serie_numero": ("SERIE", "COMPROBANTE", "TICKET", "RECIBO"),
    "subtotal": ("SUBTOTAL", "VALOR VENTA", "BASE IMPONIBLE"),
    "igv": ("IGV", "I.G.V", "IMPUESTO"),
    "monto_total": ("TOTAL A PAGAR", "IMPORTE TOTAL", "MONTO TOTAL", "TOTAL", "IMPORTE"),
    "moneda": ("MONEDA", "PEN", "SOLES", "USD"),
}

CORPORATE_TOKENS = (
    "CONCESIONARIA",
    "AUTOPISTA",
    "NORVIAL",
    "COVIPERU",
    "S.A.C",
    "SAC",
    "S.A.",
)
MONEY_PATTERN = re.compile(
    r"(?:S/\.?|PEN|US\$|USD|\$)?\s*(-?\d{1,3}(?:[.,]\d{3})*[.,]\d{2}|-?\d+[.,]\d{1,2})",
    re.IGNORECASE,
)
PLATE_RAW_PATTERN = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{2,3}[\s-]?[A-Z0-9]{3,4})(?![A-Z0-9])", re.IGNORECASE)


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.upper())
    without_marks = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", without_marks).strip()


def _label_score(text: str, labels: Iterable[str]) -> tuple[float, str]:
    plain_text = _plain(text)
    best_label = ""
    best_score = 0.0
    for label in labels:
        current = fuzz.partial_ratio(plain_text, _plain(label)) / 100.0
        if current > best_score:
            best_score = current
            best_label = label
    return round(best_score, 4), best_label


def _center_y(line: OCRLine) -> float:
    if not line.coordinates:
        return 0.0
    return sum(float(point[1]) for point in line.coordinates) / len(line.coordinates)


def _page_y_limits(lines: list[OCRLine]) -> dict[int, float]:
    limits: dict[int, float] = defaultdict(lambda: 1.0)
    for line in lines:
        if line.coordinates:
            limits[line.page] = max(
                limits[line.page],
                max(float(point[1]) for point in line.coordinates),
            )
    return dict(limits)


def _position_score(field: str, line: OCRLine, page_limit: float) -> float:
    ratio = _center_y(line) / max(1.0, page_limit)
    if field in {"concesionaria", "lugar", "estacion", "fecha", "serie_numero"}:
        return 1.0 if ratio <= 0.55 else 0.65
    if field in {"subtotal", "igv", "monto_total", "moneda"}:
        return 1.0 if ratio >= 0.45 else 0.55
    if field == "placa":
        return 1.0 if 0.15 <= ratio <= 0.80 else 0.70
    return 0.80


def _candidate(
    field: str,
    line: OCRLine,
    value: str,
    format_score: float,
    label_score: float,
    nearby_label: str,
    page_limit: float,
    consistency_score: float = 0.75,
    source: ValueSource = ValueSource.OCR,
    warnings: list[str] | None = None,
) -> FieldCandidate:
    ocr_score = 1.0 if source == ValueSource.QR else line.confidence
    position = 1.0 if source == ValueSource.QR else _position_score(field, line, page_limit)
    final = score_candidate(
        ocr_score,
        format_score,
        label_score,
        position,
        consistency_score,
    )
    return FieldCandidate(
        field=field,
        raw_text=line.text,
        value=value,
        ocr_confidence=round(ocr_score, 4),
        format_score=round(format_score, 4),
        label_score=round(label_score, 4),
        position_score=round(position, 4),
        consistency_score=round(consistency_score, 4),
        final_score=final,
        nearby_label=nearby_label,
        coordinates=line.coordinates,
        page=line.page,
        source=source,
        warnings=warnings or [],
    )


def _after_label(text: str) -> str:
    for separator in (":", "="):
        if separator in text:
            return text.split(separator, maxsplit=1)[1].strip(" -")
    return ""


def _nearby_label_score(
    lines: list[OCRLine],
    index: int,
    field: str,
) -> tuple[float, str]:
    current = lines[index]
    options = [(current.text, 1.0)]
    if index > 0 and lines[index - 1].page == current.page:
        options.append((lines[index - 1].text, 0.92))
    if index + 1 < len(lines) and lines[index + 1].page == current.page:
        options.append((lines[index + 1].text, 0.88))
    best_score = 0.0
    best_label = ""
    for text, proximity in options:
        score, label = _label_score(text, FIELD_LABELS[field])
        score *= proximity
        if score > best_score:
            best_score = score
            best_label = label
    return round(best_score, 4), best_label


def _text_candidates(lines: list[OCRLine], page_limits: dict[int, float]) -> list[FieldCandidate]:
    candidates: list[FieldCandidate] = []
    text_fields = ("concesionaria", "lugar", "estacion")
    for index, line in enumerate(lines):
        for field in text_fields:
            label_score, label = _label_score(line.text, FIELD_LABELS[field])
            if label_score < 0.72:
                continue
            value = _after_label(line.text)
            source_line = line
            if not value and index + 1 < len(lines) and lines[index + 1].page == line.page:
                next_line = lines[index + 1]
                if not any(_label_score(next_line.text, labels)[0] >= 0.85 for labels in FIELD_LABELS.values()):
                    value = next_line.text.strip()
                    source_line = next_line
            value = re.sub(r"\s+", " ", value).strip(" .:-")
            if len(value) < 3 or re.fullmatch(r"[\d.,/-]+", value):
                continue
            candidates.append(
                _candidate(
                    field,
                    source_line,
                    value,
                    0.90,
                    label_score,
                    label,
                    page_limits.get(source_line.page, 1.0),
                )
            )

        upper = _plain(line.text)
        if any(token in upper for token in CORPORATE_TOKENS) and len(line.text.strip()) >= 5:
            label_score, label = _label_score(line.text, FIELD_LABELS["concesionaria"])
            candidates.append(
                _candidate(
                    "concesionaria",
                    line,
                    re.sub(r"\s+", " ", line.text).strip(),
                    0.90,
                    max(0.75, label_score),
                    label or "ENCABEZADO EMPRESA",
                    page_limits.get(line.page, 1.0),
                )
            )
    return candidates


def _regex_candidates(lines: list[OCRLine], page_limits: dict[int, float]) -> list[FieldCandidate]:
    candidates: list[FieldCandidate] = []
    for index, line in enumerate(lines):
        limit = page_limits.get(line.page, 1.0)
        date_label, date_near = _nearby_label_score(lines, index, "fecha")
        for match in DATE_PATTERN.finditer(line.text):
            normalized = normalize_date(match.group(0))
            if normalized:
                candidates.append(_candidate("fecha", line, normalized, 1.0, date_label, date_near, limit))

        time_label, time_near = _nearby_label_score(lines, index, "hora")
        for match in TIME_PATTERN.finditer(line.text):
            normalized = normalize_time(match.group(0))
            if normalized:
                candidates.append(_candidate("hora", line, normalized, 1.0, time_label, time_near, limit))

        plate_label, plate_near = _nearby_label_score(lines, index, "placa")
        for match in PLATE_RAW_PATTERN.finditer(line.text.upper()):
            normalized = normalize_plate(match.group(1))
            if normalized:
                candidates.append(_candidate("placa", line, normalized, 1.0, plate_label, plate_near, limit))

        series_label, series_near = _nearby_label_score(lines, index, "serie_numero")
        for match in SERIES_PATTERN.finditer(line.text.upper()):
            normalized = normalize_series_number(match.group(0))
            if normalized:
                candidates.append(_candidate("serie_numero", line, normalized, 1.0, series_label, series_near, limit))

        currency = currency_from_text(line.text)
        if currency:
            currency_label, currency_near = _label_score(line.text, FIELD_LABELS["moneda"])
            candidates.append(_candidate("moneda", line, currency, 1.0, max(0.80, currency_label), currency_near, limit))

        amounts = list(MONEY_PATTERN.finditer(line.text))
        if not amounts:
            continue
        amount = normalize_decimal(amounts[-1].group(1))
        if amount is None:
            continue
        for field in ("subtotal", "igv", "monto_total"):
            plain_line = _plain(line.text)
            if field == "subtotal" and not any(
                label in plain_line
                for label in ("SUBTOTAL", "BASE IMPONIBLE", "VALOR VENTA")
            ):
                continue
            if field == "igv" and not re.search(r"\bI\.?G\.?V\.?\b|\bIMPUESTO\b", plain_line):
                continue
            if field == "monto_total" and any(
                excluded in plain_line
                for excluded in ("SUBTOTAL", "IGV", "I.G.V", "BASE IMPONIBLE", "VALOR VENTA")
            ):
                continue
            label_score, nearby_label = _label_score(line.text, FIELD_LABELS[field])
            required_label = 0.65 if field == "monto_total" else 0.72
            if label_score < required_label:
                continue
            candidates.append(
                _candidate(
                    field,
                    line,
                    decimal_text(amount),
                    1.0 if amount >= Decimal("0") else 0.0,
                    label_score,
                    nearby_label,
                    limit,
                    consistency_score=0.70,
                )
            )
    return candidates


def _qr_candidates(pages: list[PageOCRResult]) -> list[FieldCandidate]:
    candidates: list[FieldCandidate] = []
    allowed = {"fecha", "serie_numero", "igv", "monto_total", "placa", "moneda"}
    for page in pages:
        if not page.qr.valid:
            continue
        synthetic = OCRLine(
            text=page.qr.raw_text,
            confidence=1.0,
            coordinates=page.qr.coordinates,
            page=page.page,
            preprocessing_version=page.qr.source_image,
        )
        for field, raw_value in page.qr.fields.items():
            if field not in allowed or raw_value in (None, ""):
                continue
            value = str(raw_value)
            candidates.append(
                _candidate(
                    field,
                    synthetic,
                    value,
                    1.0,
                    1.0,
                    "QR VALIDADO",
                    1.0,
                    consistency_score=1.0,
                    source=ValueSource.QR,
                )
            )
    return candidates


def extract_toll_candidates(pages: list[PageOCRResult]) -> dict[str, list[FieldCandidate]]:
    lines = [line for page in pages for line in page.lines]
    page_limits = _page_y_limits(lines)
    all_candidates = (
        _text_candidates(lines, page_limits)
        + _regex_candidates(lines, page_limits)
        + _qr_candidates(pages)
    )
    grouped: dict[str, list[FieldCandidate]] = defaultdict(list)
    for candidate in all_candidates:
        grouped[candidate.field].append(candidate)
    return {
        field: sorted(values, key=lambda item: item.final_score, reverse=True)
        for field, values in grouped.items()
    }


def toll_lines_need_recovery(lines: list[OCRLine]) -> bool:
    """Decide si A necesita B/C por ausencia de campos mínimos de peaje."""
    text = "\n".join(line.text for line in lines)
    has_date = normalize_date(text) is not None
    has_plate = any(normalize_plate(match.group(1)) for match in PLATE_RAW_PATTERN.finditer(text.upper()))
    has_total = any(
        _label_score(line.text, FIELD_LABELS["monto_total"])[0] >= 0.65
        and MONEY_PATTERN.search(line.text)
        for line in lines
    )
    return not (has_date and has_plate and has_total)
