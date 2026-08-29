"""Selección y validación estructurada de peajes a partir de candidatos."""

from __future__ import annotations

from decimal import Decimal

from candidate_extractor import extract_toll_candidates
from confidence import calculated_field, global_confidence, select_field_result
from config import CONFIDENCE_REVIEW, PEAJE_REQUIRED_FIELDS
from models import (
    DocumentType,
    ExtractionResult,
    FieldResult,
    PageOCRResult,
    ValueSource,
)
from validators import decimal_text, monetary_validation, normalize_decimal


PEAJE_FIELDS = (
    "concesionaria",
    "lugar",
    "estacion",
    "fecha",
    "hora",
    "placa",
    "serie_numero",
    "subtotal",
    "igv",
    "monto_total",
    "moneda",
)


def _decimal_field(fields: dict[str, FieldResult], name: str) -> Decimal | None:
    result = fields.get(name)
    if result is None or result.value is None:
        return None
    return normalize_decimal(result.value)


def _default_currency(fields: dict[str, FieldResult]) -> None:
    if fields["moneda"].value is not None:
        return
    if any(fields[name].value is not None for name in ("subtotal", "igv", "monto_total")):
        fields["moneda"] = FieldResult(
            value="PEN",
            confidence_ocr=0.0,
            confidence_rule=0.80,
            confidence_final=0.80,
            source=ValueSource.REGLA,
            warnings=["Moneda inferida por formato S/ o contexto de peaje peruano; revisar."],
        )


def parse_toll(pages: list[PageOCRResult]) -> ExtractionResult:
    """Elige valores solo sobre el umbral y bloquea inconsistencias monetarias."""
    candidates = extract_toll_candidates(pages)
    fields = {
        field: select_field_result(candidates.get(field, []))
        for field in PEAJE_FIELDS
    }

    total = _decimal_field(fields, "monto_total")
    igv = _decimal_field(fields, "igv")
    subtotal = _decimal_field(fields, "subtotal")
    if subtotal is None and total is not None and igv is not None and total >= igv:
        calculated = total - igv
        confidence = min(
            fields["monto_total"].confidence_final,
            fields["igv"].confidence_final,
        )
        fields["subtotal"] = calculated_field(
            decimal_text(calculated),
            confidence,
            "Subtotal calculado como total menos IGV.",
        )
        subtotal = calculated

    _default_currency(fields)
    money_valid, monetary_warnings = monetary_validation(subtotal, igv, total)
    blocking: list[str] = []
    warnings: list[str] = []

    if not money_valid:
        blocking.extend(monetary_warnings)
    distinct_totals = {
        candidate.value
        for candidate in candidates.get("monto_total", [])
        if candidate.final_score >= CONFIDENCE_REVIEW
    }
    if len(distinct_totals) > 1:
        warnings.append("Se detectaron múltiples importes totales plausibles.")
        fields["monto_total"].warnings.append(
            "Revisar los múltiples totales encontrados en el documento."
        )

    missing = [name for name in PEAJE_REQUIRED_FIELDS if fields[name].value is None]
    if missing:
        blocking.append("Faltan campos obligatorios: " + ", ".join(missing) + ".")

    confidence = global_confidence(fields, PEAJE_REQUIRED_FIELDS)
    valid = confidence >= CONFIDENCE_REVIEW and not blocking
    for field in fields.values():
        warnings.extend(field.warnings)

    return ExtractionResult(
        document_type=DocumentType.PEAJE,
        fields=fields,
        global_confidence=confidence,
        valid=valid,
        blocking_validations=list(dict.fromkeys(blocking)),
        warnings=list(dict.fromkeys(warnings)),
    )
