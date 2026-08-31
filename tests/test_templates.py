"""Pruebas de conocimiento persistente y regiones de proveedor."""

from __future__ import annotations

from models import DocumentType, ExtractionResult, FieldResult, OCRLine, PageOCRResult, QRResult, QualityLevel, QualityReport
from templates import KnowledgeBase, canonical_reason_social, match_template


def test_reason_social_variant_is_canonicalized() -> None:
    knowledge = KnowledgeBase(
        dictionaries=[
            {
                "tipo": "RAZON_SOCIAL",
                "valor_canonico": "TRANSPORTES DEL PACIFICO S.A.C.",
                "variante": "TRANSP. PACIFICO SAC",
                "proveedor": "",
                "activo": "TRUE",
            }
        ]
    )
    value, score, warning = canonical_reason_social(
        None,
        "TRANSP PACIFICO SAC",
        knowledge,
    )
    assert value == "TRANSPORTES DEL PACIFICO S.A.C."
    assert score >= 0.90
    assert warning


def test_template_matches_words_and_parses_normalized_region() -> None:
    quality = QualityReport(
        width=1000,
        height=1500,
        blur_score=100,
        brightness=140,
        contrast=45,
        rotation_degrees=0,
        perspective_score=0.9,
        document_area_ratio=0.8,
        cut_border_score=0.05,
        level=QualityLevel.BUENA,
    )
    page = PageOCRResult(
        page=1,
        quality=quality,
        qr=QRResult(),
        lines=[
            OCRLine(
                text="ACME SERVICIOS FACTURA ELECTRONICA",
                confidence=0.95,
                coordinates=[],
                page=1,
                preprocessing_version="A",
            )
        ],
    )
    extraction = ExtractionResult(
        document_type=DocumentType.FACTURA,
        fields={"razon_social": FieldResult(value=None)},
    )
    knowledge = KnowledgeBase(
        templates=[
            {
                "proveedor": "ACME SERVICIOS S.A.C.",
                "palabras_identificadoras": "ACME SERVICIOS|FACTURA ELECTRONICA",
                "posicion_ruc": "0.60,0.05,0.95,0.18",
                "posicion_fecha": "",
                "posicion_placa": "",
                "posicion_total": "[0.60, 0.80, 0.95, 0.95]",
                "activo": "TRUE",
            }
        ]
    )
    matched = match_template(extraction, [page], knowledge)
    assert matched is not None
    assert matched.provider == "ACME SERVICIOS S.A.C."
    assert matched.regions["ruc_emisor"] == (0.60, 0.05, 0.95, 0.18)
    assert matched.regions["monto_total"] == (0.60, 0.80, 0.95, 0.95)
