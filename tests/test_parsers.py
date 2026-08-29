"""Pruebas de candidatos y extracción estructurada de peajes."""

from __future__ import annotations

from candidate_extractor import extract_toll_candidates
from models import OCRLine, PageOCRResult, QRResult, QualityLevel, QualityReport
from parsers import parse_toll


def line(text: str, confidence: float, y: float) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=confidence,
        coordinates=[[10, y], [500, y], [500, y + 24], [10, y + 24]],
        page=1,
        preprocessing_version="A",
    )


def page_with_toll() -> PageOCRResult:
    return PageOCRResult(
        page=1,
        quality=QualityReport(
            width=1200,
            height=1800,
            blur_score=120,
            brightness=140,
            contrast=50,
            rotation_degrees=0,
            perspective_score=0.95,
            document_area_ratio=0.80,
            cut_border_score=0.05,
            level=QualityLevel.BUENA,
        ),
        qr=QRResult(),
        lines=[
            line("CONCESIONARIA: NORVIAL S.A.C.", 0.97, 100),
            line("LUGAR: HUACHO", 0.96, 180),
            line("ESTACION: PARAISO", 0.95, 250),
            line("FECHA: 28/08/2026 HORA: 14:35", 0.98, 330),
            line("PLACA: A1B-234", 0.97, 430),
            line("TICKET F001-00001234", 0.94, 520),
            line("SUBTOTAL S/ 10.00", 0.97, 1300),
            line("IGV S/ 1.80", 0.97, 1370),
            line("TOTAL A PAGAR S/ 11.80", 0.99, 1450),
        ],
        versions_used=["A"],
    )


def test_extracts_multiple_traceable_candidates() -> None:
    candidates = extract_toll_candidates([page_with_toll()])
    assert candidates["placa"][0].value == "A1B-234"
    assert candidates["monto_total"][0].value == "11.80"
    assert candidates["monto_total"][0].raw_text == "TOTAL A PAGAR S/ 11.80"


def test_parses_valid_toll_without_gemini() -> None:
    result = parse_toll([page_with_toll()])
    assert result.fields["fecha"].value == "2026-08-28"
    assert result.fields["placa"].value == "A1B-234"
    assert result.fields["monto_total"].value == "11.80"
    assert result.fields["moneda"].value == "PEN"
    assert result.valid
    assert result.global_confidence >= 0.75


def test_calculates_subtotal_only_when_total_and_igv_are_valid() -> None:
    page = page_with_toll()
    page.lines = [item for item in page.lines if not item.text.startswith("SUBTOTAL")]
    result = parse_toll([page])
    assert result.fields["subtotal"].value == "10.00"
    assert result.fields["subtotal"].source == "CALCULADO"
