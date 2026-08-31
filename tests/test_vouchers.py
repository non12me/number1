"""Pruebas de boletas, facturas e ítems sin llamadas externas."""

from __future__ import annotations

import json

from candidate_extractor import extract_voucher_candidates
from models import DocumentType, OCRLine, PageOCRResult, QRResult, QualityLevel, QualityReport
from parsers import parse_voucher
from templates import KnowledgeBase


def line(text: str, confidence: float, y: float) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=confidence,
        coordinates=[[10, y], [700, y], [700, y + 24], [10, y + 24]],
        page=1,
        preprocessing_version="A",
    )


def voucher_page() -> PageOCRResult:
    return PageOCRResult(
        page=1,
        quality=QualityReport(
            width=1400,
            height=2000,
            blur_score=130,
            brightness=145,
            contrast=55,
            rotation_degrees=0,
            perspective_score=0.96,
            document_area_ratio=0.82,
            cut_border_score=0.04,
            level=QualityLevel.BUENA,
        ),
        qr=QRResult(),
        lines=[
            line("ACME SERVICIOS S.A.C.", 0.97, 80),
            line("RUC: 20100070970", 0.99, 140),
            line("FACTURA ELECTRONICA F001-00001234", 0.97, 220),
            line("FECHA EMISION: 28/08/2026", 0.98, 290),
            line("CLIENTE DNI: 12345678", 0.96, 380),
            line("CANT. DESCRIPCION P.UNIT TOTAL", 0.95, 650),
            line("1 SERVICIO DE TRANSPORTE 100.00 100.00", 0.94, 720),
            line("SUBTOTAL S/ 100.00", 0.98, 1500),
            line("IGV S/ 18.00", 0.98, 1570),
            line("TOTAL A PAGAR S/ 118.00", 0.99, 1640),
        ],
        versions_used=["A"],
    )


def test_voucher_candidates_prioritize_valid_issuer_ruc() -> None:
    candidates = extract_voucher_candidates([voucher_page()])
    assert candidates["ruc_emisor"][0].value == "20100070970"
    assert candidates["ruc_emisor"][0].format_score == 1.0
    assert candidates["serie_numero"][0].value == "F001-00001234"


def test_invoice_extracts_fields_items_and_summary() -> None:
    result = parse_voucher(
        [voucher_page()],
        DocumentType.FACTURA,
        KnowledgeBase(),
    )
    assert result.fields["ruc_emisor"].value == "20100070970"
    assert result.fields["razon_social"].value == "ACME SERVICIOS S.A.C."
    assert result.fields["documento_cliente"].value == "12345678"
    assert result.fields["monto_total"].value == "118.00"
    assert "TRANSPORTE" in (result.fields["concepto_resumen"].value or "")
    items = json.loads(result.fields["items_json"].value or "[]")
    assert len(items) == 1
    assert items[0]["quantity"] == "1"
    assert items[0]["total"] == "100.00"
    assert result.valid


def test_boleta_uses_same_validated_parser() -> None:
    result = parse_voucher(
        [voucher_page()],
        DocumentType.BOLETA,
        KnowledgeBase(),
    )
    assert result.document_type == DocumentType.BOLETA
    assert result.fields["fecha"].value == "2026-08-28"


def test_verified_ruc_dictionary_replaces_reason_social() -> None:
    knowledge = KnowledgeBase(
        dictionaries=[
            {
                "tipo": "RUC_PROVEEDOR",
                "valor_canonico": "20100070970",
                "variante": "",
                "proveedor": "ACME SERVICIOS DEL PERU S.A.C.",
                "activo": "TRUE",
            }
        ]
    )
    result = parse_voucher(
        [voucher_page()],
        DocumentType.FACTURA,
        knowledge,
    )
    assert result.fields["razon_social"].value == "ACME SERVICIOS DEL PERU S.A.C."
    assert result.fields["razon_social"].source == "REGLA"


def test_invalid_issuer_ruc_is_not_accepted() -> None:
    page = voucher_page()
    page.lines[1] = line("RUC: 20100070971", 0.99, 140)
    result = parse_voucher([page], DocumentType.FACTURA, KnowledgeBase())
    assert result.fields["ruc_emisor"].value is None
    assert not result.valid
