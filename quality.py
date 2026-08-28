"""Evaluación objetiva de calidad antes de ejecutar OCR."""

from __future__ import annotations

import math

import cv2
import numpy as np

from models import QualityLevel, QualityReport


def _order_points(points: np.ndarray) -> np.ndarray:
    points = points.astype("float32")
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).reshape(-1)
    return np.array(
        [
            points[np.argmin(sums)],
            points[np.argmin(differences)],
            points[np.argmax(sums)],
            points[np.argmax(differences)],
        ],
        dtype="float32",
    )


def find_document_quad(image: np.ndarray) -> np.ndarray | None:
    """Busca el mayor contorno cuadrilateral que parezca un documento."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)),
        iterations=2,
    )
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_area = float(image.shape[0] * image.shape[1])
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        if cv2.contourArea(contour) < image_area * 0.18:
            continue
        perimeter = cv2.arcLength(contour, True)
        approximation = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approximation) == 4 and cv2.isContourConvex(approximation):
            return _order_points(approximation.reshape(4, 2))
    return None


def estimate_rotation(gray: np.ndarray) -> float:
    """Estima inclinación pequeña a partir de líneas casi horizontales."""
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    minimum_length = max(40, gray.shape[1] // 5)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=60,
        minLineLength=minimum_length,
        maxLineGap=20,
    )
    if lines is None:
        return 0.0
    angles: list[float] = []
    for x1, y1, x2, y2 in lines[:, 0]:
        angle = math.degrees(math.atan2(float(y2 - y1), float(x2 - x1)))
        while angle <= -90:
            angle += 180
        while angle > 90:
            angle -= 180
        if abs(angle) <= 20:
            angles.append(angle)
    return round(float(np.median(angles)), 2) if angles else 0.0


def _perspective_score(quad: np.ndarray | None) -> float:
    """Devuelve 1 para geometría rectangular y valores menores si hay fuga."""
    if quad is None:
        return 0.5
    top_left, top_right, bottom_right, bottom_left = quad
    top = np.linalg.norm(top_right - top_left)
    bottom = np.linalg.norm(bottom_right - bottom_left)
    left = np.linalg.norm(bottom_left - top_left)
    right = np.linalg.norm(bottom_right - top_right)
    horizontal = min(top, bottom) / max(top, bottom, 1.0)
    vertical = min(left, right) / max(left, right, 1.0)
    return round(float(max(0.0, min(1.0, (horizontal + vertical) / 2))), 3)


def _document_area_ratio(image: np.ndarray, quad: np.ndarray | None) -> float:
    if quad is None:
        return 0.0
    area = cv2.contourArea(quad.astype(np.float32))
    return round(float(area / max(1, image.shape[0] * image.shape[1])), 3)


def _cut_border_score(gray: np.ndarray) -> float:
    edges = cv2.Canny(gray, 50, 150)
    border = max(3, int(min(gray.shape) * 0.02))
    mask = np.zeros_like(edges, dtype=np.uint8)
    mask[:border, :] = 1
    mask[-border:, :] = 1
    mask[:, :border] = 1
    mask[:, -border:] = 1
    edge_count = int(np.count_nonzero(edges))
    if edge_count == 0:
        return 0.0
    return round(float(np.count_nonzero(edges * mask) / edge_count), 3)


def _classify_quality(
    width: int,
    height: int,
    blur: float,
    brightness: float,
    contrast: float,
    cut_border: float,
) -> tuple[QualityLevel, list[str]]:
    warnings: list[str] = []
    short_side = min(width, height)

    if short_side < 500:
        warnings.append("Resolución demasiado baja.")
    elif short_side < 900:
        warnings.append("Resolución limitada para texto pequeño.")
    if blur < 25:
        warnings.append("Desenfoque severo.")
    elif blur < 70:
        warnings.append("La imagen está desenfocada.")
    if brightness < 25:
        warnings.append("Imagen extremadamente oscura.")
    elif brightness < 55:
        warnings.append("Imagen oscura.")
    if brightness > 245:
        warnings.append("Imagen extremadamente sobreexpuesta.")
    elif brightness > 225:
        warnings.append("Imagen sobreexpuesta.")
    if contrast < 12:
        warnings.append("Contraste insuficiente.")
    elif contrast < 25:
        warnings.append("Contraste bajo.")
    if cut_border > 0.30:
        warnings.append("Posibles bordes o texto cortados.")

    illegible = (
        short_side < 420
        or blur < 12
        or contrast < 7
        or brightness < 12
        or brightness > 251
    )
    deficient = (
        short_side < 650
        or blur < 35
        or contrast < 16
        or brightness < 38
        or brightness > 238
        or cut_border > 0.42
    )
    acceptable = (
        short_side < 1000
        or blur < 90
        or contrast < 32
        or brightness < 60
        or brightness > 220
        or cut_border > 0.25
    )
    if illegible:
        return QualityLevel.ILEGIBLE, warnings
    if deficient:
        return QualityLevel.DEFICIENTE, warnings
    if acceptable:
        return QualityLevel.ACEPTABLE, warnings
    return QualityLevel.BUENA, warnings


def assess_image_quality(image: np.ndarray) -> QualityReport:
    """Calcula resolución, enfoque, luz, contraste y geometría."""
    if image is None or image.size == 0:
        raise ValueError("La página no contiene una imagen válida.")
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    contrast = float(gray.std())
    rotation = estimate_rotation(gray)
    quad = find_document_quad(image)
    perspective = _perspective_score(quad)
    area_ratio = _document_area_ratio(image, quad)
    cut_border = _cut_border_score(gray)
    level, warnings = _classify_quality(
        width,
        height,
        blur,
        brightness,
        contrast,
        cut_border,
    )
    if abs(rotation) >= 2.0:
        warnings.append(f"Inclinación aproximada de {rotation:.1f} grados.")
    if quad is not None and perspective < 0.78:
        warnings.append("Perspectiva marcada.")
    if quad is not None and area_ratio < 0.35:
        warnings.append("El documento ocupa una parte pequeña de la imagen.")

    return QualityReport(
        width=width,
        height=height,
        blur_score=round(blur, 2),
        brightness=round(brightness, 2),
        contrast=round(contrast, 2),
        rotation_degrees=rotation,
        perspective_score=perspective,
        document_area_ratio=area_ratio,
        cut_border_score=cut_border,
        level=level,
        warnings=warnings,
    )
