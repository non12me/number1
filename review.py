"""Normalización y revisión humana determinista de resultados OCR."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from config import PEAJE_REQUIRED_FIELDS, VOUCHER_REQUIRED_FIELDS
from models import DocumentType, ValueSource, utc_now_iso
from validators import (
    decimal_text,
    monetary_validation,
    normalize_date,
    normalize_decimal,
    normalize_plate,
    normalize_series_number,
    normalize_time,
    validate_dni,
    validate_peruvian_ruc,
)


FIELD_ORDER = {
    DocumentType.PEAJE: (
        "concesionaria", "lugar", "estacion", "fecha", "hora", "placa",
        "serie_numero", "subtotal", "igv", "monto_total", "moneda",
    ),
    DocumentType.BOLETA: (
        "ruc_emisor", "razon_social", "serie_numero", "fecha",
        "documento_cliente", "concepto_resumen", "items_json", "subtotal",
        "igv", "monto_total", "moneda",
    ),
    DocumentType.FACTURA: (
        "ruc_emisor", "razon_social", "serie_numero", "fecha",
        "documento_cliente", "concepto_resumen", "items_json", "subtotal",
        "igv", "monto_total", "moneda",
    ),
}

FIELD_LABELS = {
    "concesionaria": "Concesionaria",
    "lugar": "Lugar",
    "estacion": "Estación",
    "fecha": "Fecha",
    "hora": "Hora",
    "placa": "Placa",
    "serie_numero": "Serie y número",
    "ruc_emisor": "RUC emisor",
    "razon_social": "Razón social",
    "documento_cliente": "DNI/documento del cliente",
    "concepto_resumen": "Concepto resumido",
    "items_json": "Ítems JSON",
    "subtotal": "Subtotal",
    "igv": "IGV",
    "monto_total": "Monto total",
    "moneda": "Moneda",
}

MONEY_FIELDS = ("subtotal", "igv", "monto_total")


@dataclass(slots=True)
class ReviewValidation:
    valid: bool
    normalized_values: dict[str, str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def field_names(document_type: DocumentType | str) -> tuple[str, ...]:
    """Devuelve el orden canónico de campos editables."""
    return FIELD_ORDER[DocumentType(document_type)]


def values_from_payload(
    payload: dict[str, Any],
    document_type: DocumentType | str,
) -> dict[str, str]:
    """Extrae valores editables sin perder campos ausentes."""
    fields = ((payload.get("extraction") or {}).get("fields") or {})
    return {
        name: str((fields.get(name) or {}).get("value") or "")
        for name in field_names(document_type)
    }


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _review_date(value: str) -> str | None:
    text = value.strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return normalize_date(text)
    if normalize_date(parsed.strftime("%d/%m/%Y")) is None:
        return None
    return parsed.date().isoformat()


def _items_json(value: str) -> tuple[str, str | None]:
    if not value.strip():
        return "", None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value.strip(), "Los ítems no contienen JSON válido."
    if not isinstance(parsed, list):
        return value.strip(), "items_json debe contener una lista JSON."
    if any(not isinstance(item, dict) for item in parsed):
        return value.strip(), "Cada ítem debe ser un objeto JSON."
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":")), None


def validate_review_values(
    document_type: DocumentType | str,
    values: dict[str, Any],
) -> ReviewValidation:
    """Normaliza la edición humana y devuelve errores bloqueantes claros."""
    kind = DocumentType(document_type)
    normalized = {name: _clean_text(values.get(name, "")) for name in field_names(kind)}
    errors: list[str] = []
    warnings: list[str] = []

    if normalized.get("fecha"):
        date_value = _review_date(normalized["fecha"])
        if date_value is None:
            errors.append("La fecha no es válida.")
        else:
            normalized["fecha"] = date_value

    if kind == DocumentType.PEAJE:
        if normalized.get("hora"):
            time_value = normalize_time(normalized["hora"])
            if time_value is None:
                errors.append("La hora no es válida.")
            else:
                normalized["hora"] = time_value
        if normalized.get("placa"):
            plate_value = normalize_plate(normalized["placa"])
            if plate_value is None:
                errors.append("La placa no coincide con un patrón peruano admitido.")
            else:
                normalized["placa"] = plate_value
        if normalized.get("serie_numero"):
            normalized["serie_numero"] = normalized["serie_numero"].upper()
    else:
        ruc = re.sub(r"\D", "", normalized.get("ruc_emisor", ""))
        normalized["ruc_emisor"] = ruc
        if ruc and not validate_peruvian_ruc(ruc):
            errors.append("El RUC emisor no supera el dígito verificador.")
        if normalized.get("serie_numero"):
            series = normalize_series_number(normalized["serie_numero"])
            if series is None:
                errors.append("La serie y número del comprobante no son válidos.")
            else:
                normalized["serie_numero"] = series
        client_document = re.sub(r"\D", "", normalized.get("documento_cliente", ""))
        normalized["documento_cliente"] = client_document
        if client_document and not validate_dni(client_document):
            errors.append("El documento del cliente debe tener 8 dígitos.")
        items, items_error = _items_json(normalized.get("items_json", ""))
        normalized["items_json"] = items
        if items_error:
            errors.append(items_error)

    decimals = {}
    for name in MONEY_FIELDS:
        text = normalized.get(name, "")
        if not text:
            decimals[name] = None
            continue
        amount = normalize_decimal(text)
        if amount is None:
            errors.append(f"{FIELD_LABELS[name]} no es un importe válido.")
            decimals[name] = None
            continue
        decimals[name] = amount
        normalized[name] = decimal_text(amount)

    money_valid, money_warnings = monetary_validation(
        decimals.get("subtotal"), decimals.get("igv"), decimals.get("monto_total")
    )
    if not money_valid:
        errors.extend(money_warnings)

    currency = normalized.get("moneda", "").upper()
    if currency in {"S/", "SOL", "SOLES"}:
        currency = "PEN"
    elif currency in {"$", "US$", "DOLAR", "DOLARES"}:
        currency = "USD"
    normalized["moneda"] = currency
    if currency and currency not in {"PEN", "USD"}:
        errors.append("La moneda debe ser PEN o USD.")
    if not currency and decimals.get("monto_total") is not None:
        normalized["moneda"] = "PEN"
        warnings.append("Se asignó PEN porque la moneda estaba vacía.")

    required = PEAJE_REQUIRED_FIELDS if kind == DocumentType.PEAJE else VOUCHER_REQUIRED_FIELDS
    missing = [FIELD_LABELS[name] for name in required if not normalized.get(name)]
    if missing:
        errors.append("Faltan campos obligatorios: " + ", ".join(missing) + ".")

    return ReviewValidation(
        valid=not errors,
        normalized_values=normalized,
        errors=list(dict.fromkeys(errors)),
        warnings=list(dict.fromkeys(warnings)),
    )


def apply_review_values(
    payload: dict[str, Any],
    document_type: DocumentType | str,
    values: dict[str, Any],
    user_email: str,
    action: str,
) -> tuple[dict[str, Any], ReviewValidation, list[dict[str, str]]]:
    """Aplica cambios humanos conservando la evidencia OCR original."""
    kind = DocumentType(document_type)
    validation = validate_review_values(kind, values)
    updated = deepcopy(payload)
    extraction = updated.setdefault("extraction", {})
    extraction["document_type"] = kind.value
    fields = extraction.setdefault("fields", {})
    corrections: list[dict[str, str]] = []

    for name in field_names(kind):
        field_data = fields.setdefault(name, {})
        old_value = str(field_data.get("value") or "")
        new_value = validation.normalized_values.get(name, "")
        if old_value == new_value:
            continue
        corrections.append(
            {
                "job_id": "",
                "tipo_documento": kind.value,
                "proveedor": validation.normalized_values.get(
                    "razon_social", validation.normalized_values.get("concesionaria", "")
                ),
                "campo": name,
                "texto_ocr_original": str(field_data.get("raw_text") or ""),
                "valor_detectado": old_value,
                "valor_corregido": new_value,
                "usuario": user_email,
                "fecha": utc_now_iso(),
            }
        )
        field_data.update(
            {
                "value": new_value or None,
                "confidence_ocr": float(field_data.get("confidence_ocr") or 0),
                "confidence_rule": 1.0,
                "confidence_final": 1.0,
                "source": ValueSource.HUMANO.value,
                "warnings": [],
            }
        )

    extraction["valid"] = validation.valid
    extraction["blocking_validations"] = validation.errors
    extraction["warnings"] = validation.warnings
    required = PEAJE_REQUIRED_FIELDS if kind == DocumentType.PEAJE else VOUCHER_REQUIRED_FIELDS
    required_scores = [
        float((fields.get(name) or {}).get("confidence_final") or 0)
        for name in required
        if validation.normalized_values.get(name)
    ]
    extraction["global_confidence"] = round(min(required_scores), 4) if required_scores else 0.0
    updated["human_review"] = {
        "action": action,
        "reviewed_at": utc_now_iso(),
        "reviewed_by": user_email,
        "valid": validation.valid,
        "errors": validation.errors,
        "warnings": validation.warnings,
    }
    return updated, validation, corrections


def build_review_previews(data: bytes, mime_type: str, page_label: str = ""):
    """Construye original y versión A; dependencias visuales se cargan bajo pedido."""
    import cv2
    import numpy as np
    import pymupdf

    from preprocessing import preprocess_version_a

    if mime_type == "application/pdf":
        with pymupdf.open(stream=data, filetype="pdf") as document:
            page_number = 1
            if page_label:
                first_label = page_label.split("-", maxsplit=1)[0]
                if first_label.isdigit():
                    page_number = max(1, min(int(first_label), document.page_count))
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
            rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            original = (
                cv2.cvtColor(rgb, cv2.COLOR_RGBA2BGR)
                if pixmap.n == 4
                else cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            )
    else:
        encoded = np.frombuffer(data, dtype=np.uint8)
        original = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if original is None:
            raise ValueError("No se pudo generar la vista previa de la imagen.")
    return original, preprocess_version_a(original)
