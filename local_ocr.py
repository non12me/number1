"""PaddleOCR ligero en CPU, cargado una sola vez y con API alternativa."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
import streamlit as st

from config import (
    OCR_CPU_THREADS,
    OCR_FALLBACK_DETECTION_MODEL,
    OCR_FALLBACK_RECOGNITION_MODEL,
    OCR_FALLBACK_VERSION,
    OCR_LANGUAGE,
    OCR_MIN_SCORE,
    OCR_PRIMARY_DETECTION_MODEL,
    OCR_PRIMARY_RECOGNITION_MODEL,
    OCR_RECOVERY_SCORE,
    OCR_TARGET_LONG_SIDE,
    OCR_VERSION,
)
from models import OCRLine, QualityLevel
from preprocessing import (
    preprocess_version_a,
    preprocess_version_b,
    preprocess_version_c,
    segment_long_receipt,
)


class OCRModelLoadError(RuntimeError):
    """PaddleOCR no pudo cargar ningún perfil CPU compatible."""


@dataclass(frozen=True, slots=True)
class OCREngineBundle:
    engine: Any
    profile: str


def _engine_kwargs() -> dict[str, Any]:
    return {
        "lang": OCR_LANGUAGE,
        "device": "cpu",
        "engine": "paddle_static",
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "enable_hpi": False,
        "enable_mkldnn": False,
        "cpu_threads": OCR_CPU_THREADS,
        "text_det_limit_side_len": OCR_TARGET_LONG_SIDE,
        "text_det_limit_type": "max",
        "text_rec_score_thresh": 0.0,
    }


@st.cache_resource(show_spinner="Cargando el modelo OCR local por primera vez...")
def get_ocr_engine() -> OCREngineBundle:
    """Carga un único modelo compartido; nunca se recrea en cada interacción."""
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    os.environ.setdefault("FLAGS_allocator_strategy", "auto_growth")
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:  # noqa: BLE001
        raise OCRModelLoadError("PaddleOCR no está disponible en el servidor.") from exc

    primary = _engine_kwargs() | {
        "ocr_version": OCR_VERSION,
        "text_detection_model_name": OCR_PRIMARY_DETECTION_MODEL,
        "text_recognition_model_name": OCR_PRIMARY_RECOGNITION_MODEL,
    }
    try:
        return OCREngineBundle(PaddleOCR(**primary), "PP-OCRv6-TINY")
    except Exception:  # noqa: BLE001
        fallback = _engine_kwargs() | {
            "ocr_version": OCR_FALLBACK_VERSION,
            "text_detection_model_name": OCR_FALLBACK_DETECTION_MODEL,
            "text_recognition_model_name": OCR_FALLBACK_RECOGNITION_MODEL,
        }
        try:
            return OCREngineBundle(PaddleOCR(**fallback), "PP-OCRv5-MOBILE")
        except Exception as exc:  # noqa: BLE001
            raise OCRModelLoadError(
                "No se pudo cargar el modelo OCR principal ni el alternativo."
            ) from exc


def _result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result.get("res", result)
    raw_json = getattr(result, "json", None)
    if callable(raw_json):
        raw_json = raw_json()
    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except json.JSONDecodeError:
            raw_json = None
    if isinstance(raw_json, dict):
        return raw_json.get("res", raw_json)
    raw_result = getattr(result, "res", None)
    if isinstance(raw_result, dict):
        return raw_result
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return converted.get("res", converted)
    return {}


def _polygon(value: Any, y_offset: int = 0) -> list[list[float]]:
    if value is None:
        return []
    try:
        array = np.asarray(value, dtype=float).reshape(-1, 2)
    except (TypeError, ValueError):
        return []
    return [
        [round(float(x), 2), round(float(y) + y_offset, 2)]
        for x, y in array
    ]


def _parse_modern_results(
    results: Any,
    page: int,
    version: str,
    y_offset: int,
) -> list[OCRLine]:
    lines: list[OCRLine] = []
    for result in list(results or []):
        data = _result_to_dict(result)
        texts = list(data.get("rec_texts") or [])
        scores = list(data.get("rec_scores") or [])
        polygons = list(data.get("rec_polys") or data.get("dt_polys") or [])
        for index, raw_text in enumerate(texts):
            text = str(raw_text).strip()
            if not text:
                continue
            score = float(scores[index]) if index < len(scores) else 0.0
            if score < OCR_MIN_SCORE:
                continue
            coordinates = _polygon(polygons[index] if index < len(polygons) else None, y_offset)
            lines.append(
                OCRLine(
                    text=text,
                    confidence=round(max(0.0, min(1.0, score)), 4),
                    coordinates=coordinates,
                    page=page,
                    preprocessing_version=version,
                )
            )
    return lines


def _parse_legacy_results(
    results: Any,
    page: int,
    version: str,
    y_offset: int,
) -> list[OCRLine]:
    if not isinstance(results, list):
        return []
    entries = results
    if len(entries) == 1 and isinstance(entries[0], list):
        entries = entries[0]
    lines: list[OCRLine] = []
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        box, recognition = entry[0], entry[1]
        if not isinstance(recognition, (list, tuple)) or len(recognition) < 2:
            continue
        text = str(recognition[0]).strip()
        score = float(recognition[1])
        if not text or score < OCR_MIN_SCORE:
            continue
        lines.append(
            OCRLine(
                text=text,
                confidence=round(max(0.0, min(1.0, score)), 4),
                coordinates=_polygon(box, y_offset),
                page=page,
                preprocessing_version=version,
            )
        )
    return lines


def _predict_segment(
    bundle: OCREngineBundle,
    image: np.ndarray,
    page: int,
    version: str,
    y_offset: int,
) -> list[OCRLine]:
    input_image = image
    if image.ndim == 2:
        input_image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    modern_error: Exception | None = None
    try:
        results = bundle.engine.predict(
            input_image,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        parsed = _parse_modern_results(results, page, version, y_offset)
        return parsed
    except Exception as exc:  # noqa: BLE001
        modern_error = exc

    legacy_method = getattr(bundle.engine, "ocr", None)
    if not callable(legacy_method):
        if modern_error is not None:
            raise RuntimeError("Fallaron las dos rutas de inferencia OCR.") from modern_error
        return []
    try:
        try:
            legacy_results = legacy_method(input_image, cls=False)
        except TypeError:
            legacy_results = legacy_method(
                input_image,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        modern_lines = _parse_modern_results(
            legacy_results,
            page,
            version,
            y_offset,
        )
        if modern_lines:
            return modern_lines
        return _parse_legacy_results(legacy_results, page, version, y_offset)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Fallaron las dos rutas de inferencia OCR.") from exc


def _ocr_version(
    bundle: OCREngineBundle,
    image: np.ndarray,
    page: int,
    version: str,
) -> list[OCRLine]:
    lines: list[OCRLine] = []
    for segment in segment_long_receipt(image):
        lines.extend(
            _predict_segment(
                bundle,
                segment.image,
                page,
                version,
                segment.y_offset,
            )
        )
    return lines


def _normalized_text(text: str) -> str:
    return re.sub(r"\W+", "", text.casefold(), flags=re.UNICODE)


def _center(line: OCRLine) -> tuple[float, float] | None:
    if not line.coordinates:
        return None
    array = np.asarray(line.coordinates, dtype=float)
    return float(array[:, 0].mean()), float(array[:, 1].mean())


def _same_location(left: OCRLine, right: OCRLine) -> bool:
    left_center = _center(left)
    right_center = _center(right)
    if left_center is None or right_center is None:
        return True
    distance = np.linalg.norm(np.asarray(left_center) - np.asarray(right_center))
    return bool(distance <= 65)


def combine_ocr_lines(groups: list[list[OCRLine]]) -> list[OCRLine]:
    """Combina versiones conservando repeticiones reales en posiciones distintas."""
    selected: list[OCRLine] = []
    for line in (item for group in groups for item in group):
        normalized = _normalized_text(line.text)
        duplicate_index = None
        for index, existing in enumerate(selected):
            if (
                existing.page == line.page
                and _normalized_text(existing.text) == normalized
                and _same_location(existing, line)
            ):
                duplicate_index = index
                break
        if duplicate_index is None:
            selected.append(line)
        elif line.confidence > selected[duplicate_index].confidence:
            selected[duplicate_index] = line

    def sort_key(line: OCRLine) -> tuple[int, float, float]:
        center = _center(line) or (0.0, 0.0)
        return line.page, center[1], center[0]

    return sorted(selected, key=sort_key)


def _weak(lines: list[OCRLine]) -> bool:
    if len(lines) < 3:
        return True
    mean_score = sum(line.confidence for line in lines) / len(lines)
    return mean_score < OCR_RECOVERY_SCORE


def run_adaptive_ocr(
    original: np.ndarray,
    page: int,
    quality_level: QualityLevel,
    recovery_evaluator: Callable[[list[OCRLine]], bool] | None = None,
) -> tuple[str, list[OCRLine], list[str], np.ndarray]:
    """Ejecuta A y solo añade B/C cuando la evidencia lo requiere."""
    bundle = get_ocr_engine()
    version_a = preprocess_version_a(original)
    lines_a = _ocr_version(bundle, version_a, page, "A")
    groups = [lines_a]
    versions = ["A"]
    combined = combine_ocr_lines(groups)

    needs_recovery = _weak(combined) or (
        recovery_evaluator(combined) if recovery_evaluator is not None else False
    )
    if quality_level != QualityLevel.BUENA or needs_recovery:
        version_b = preprocess_version_b(version_a)
        groups.append(_ocr_version(bundle, version_b, page, "B"))
        versions.append("B")
        combined = combine_ocr_lines(groups)

    needs_recovery = _weak(combined) or (
        recovery_evaluator(combined) if recovery_evaluator is not None else False
    )
    if quality_level in {QualityLevel.DEFICIENTE, QualityLevel.ILEGIBLE} or needs_recovery:
        version_c = preprocess_version_c(version_a)
        groups.append(_ocr_version(bundle, version_c, page, "C"))
        versions.append("C")
        combined = combine_ocr_lines(groups)

    return bundle.profile, combined, versions, version_a
