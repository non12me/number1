"""Pruebas de calidad y preprocesamiento con imágenes sintéticas."""

from __future__ import annotations

import cv2
import numpy as np

from models import QualityLevel
from preprocessing import (
    preprocess_version_a,
    preprocess_version_b,
    preprocess_version_c,
    segment_long_receipt,
)
from quality import assess_image_quality


def synthetic_document(width: int = 1000, height: int = 1400) -> np.ndarray:
    image = np.full((height, width, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (40, 40), (width - 40, height - 40), (30, 30, 30), 4)
    for index in range(14):
        y = 120 + index * 75
        cv2.putText(
            image,
            f"PEAJE LINEA {index:02d} TOTAL 25.50",
            (90, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (10, 10, 10),
            2,
            cv2.LINE_AA,
        )
    return image


def test_blank_image_is_illegible() -> None:
    blank = np.full((600, 800, 3), 255, dtype=np.uint8)
    report = assess_image_quality(blank)
    assert report.level == QualityLevel.ILEGIBLE
    assert report.contrast == 0


def test_quality_report_contains_all_geometry_metrics() -> None:
    report = assess_image_quality(synthetic_document())
    assert report.width == 1000
    assert report.height == 1400
    assert report.blur_score > 0
    assert 0 <= report.perspective_score <= 1
    assert 0 <= report.cut_border_score <= 1


def test_three_versions_do_not_modify_original() -> None:
    original = synthetic_document()
    untouched = original.copy()
    version_a = preprocess_version_a(original)
    version_b = preprocess_version_b(version_a)
    version_c = preprocess_version_c(version_a)
    assert np.array_equal(original, untouched)
    assert version_a.ndim == 3
    assert version_b.ndim == 2
    assert version_c.ndim == 2


def test_long_receipt_is_segmented_with_offsets() -> None:
    receipt = synthetic_document(width=600, height=4000)
    segments = segment_long_receipt(receipt)
    assert len(segments) >= 2
    assert segments[0].y_offset == 0
    assert segments[1].y_offset > 0
