"""Pruebas de normalización y edición humana de la Fase 7."""

from __future__ import annotations

import json

import cv2
import numpy as np

from models import DocumentType
from review import apply_review_values, build_review_previews, validate_review_values


def valid_peaje() -> dict[str, str]:
    return {
        "concesionaria": "Concesión Sur",
        "lugar": "Lurín",
        "estacion": "Km 25",
        "fecha": "28/08/2026",
        "hora": "7:05",
        "placa": "ABC123",
        "serie_numero": "T001-0001",
        "subtotal": "10,00",
        "igv": "1,80",
        "monto_total": "11,80",
        "moneda": "S/",
    }


def test_peaje_review_normalizes_all_critical_fields() -> None:
    result = validate_review_values(DocumentType.PEAJE, valid_peaje())
    assert result.valid
    assert result.normalized_values["fecha"] == "2026-08-28"
    assert result.normalized_values["hora"] == "07:05"
    assert result.normalized_values["placa"] == "ABC-123"
    assert result.normalized_values["monto_total"] == "11.80"
    assert result.normalized_values["moneda"] == "PEN"


def test_review_blocks_inconsistent_money() -> None:
    values = valid_peaje()
    values["monto_total"] = "9.00"
    result = validate_review_values(DocumentType.PEAJE, values)
    assert not result.valid
    assert any("Subtotal más IGV" in error for error in result.errors)


def test_voucher_review_checks_ruc_series_dni_and_items() -> None:
    values = {
        "ruc_emisor": "20100070970",
        "razon_social": "ACME S.A.C.",
        "serie_numero": "F001 - 00001234",
        "fecha": "2026-08-28",
        "documento_cliente": "12345678",
        "concepto_resumen": "Servicio",
        "items_json": json.dumps([{"description": "Servicio", "total": "100.00"}]),
        "subtotal": "100",
        "igv": "18",
        "monto_total": "118",
        "moneda": "PEN",
    }
    result = validate_review_values(DocumentType.FACTURA, values)
    assert result.valid
    assert result.normalized_values["serie_numero"] == "F001-00001234"
    assert json.loads(result.normalized_values["items_json"])[0]["total"] == "100.00"


def test_apply_review_marks_changed_value_as_human() -> None:
    normalized = validate_review_values(DocumentType.PEAJE, valid_peaje()).normalized_values
    payload = {
        "extraction": {
            "document_type": "PEAJE",
            "fields": {
                name: {
                    "value": value,
                    "confidence_final": 0.80,
                    "confidence_ocr": 0.80,
                    "source": "OCR",
                    "raw_text": value,
                }
                for name, value in normalized.items()
            },
        }
    }
    values = dict(normalized)
    values["lugar"] = "Chilca"
    updated, validation, corrections = apply_review_values(
        payload, DocumentType.PEAJE, values, "admin@example.com", "CONFIRMAR"
    )
    assert validation.valid
    assert [item["campo"] for item in corrections] == ["lugar"]
    assert updated["extraction"]["fields"]["lugar"]["source"] == "HUMANO"
    assert updated["human_review"]["reviewed_by"] == "admin@example.com"


def test_preview_returns_original_and_processed_images() -> None:
    image = np.full((500, 350, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (30, 30), (320, 470), (20, 20, 20), 3)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    original, processed = build_review_previews(encoded.tobytes(), "image/jpeg")
    assert original.shape[:2] == (500, 350)
    assert processed.size > 0
