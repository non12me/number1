"""Pruebas de combinación de líneas sin cargar modelos."""

from __future__ import annotations

from local_ocr import combine_ocr_lines
from models import OCRLine


def make_line(text: str, confidence: float, y: float, version: str) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=confidence,
        coordinates=[[10, y], [200, y], [200, y + 20], [10, y + 20]],
        page=1,
        preprocessing_version=version,
    )


def test_combine_keeps_best_duplicate_and_real_repetition() -> None:
    groups = [
        [make_line("TOTAL 25.50", 0.70, 100, "A")],
        [
            make_line("TOTAL 25.50", 0.95, 102, "B"),
            make_line("TOTAL 25.50", 0.90, 500, "B"),
        ],
    ]
    combined = combine_ocr_lines(groups)
    assert len(combined) == 2
    assert combined[0].confidence == 0.95
    assert combined[0].preprocessing_version == "B"
