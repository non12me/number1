"""Lectura local de QR y validación conservadora de comprobantes peruanos."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import cv2
import numpy as np

from models import QRResult


def validate_peruvian_ruc(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False

    weights = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    total = sum(
        int(digits[index]) * weights[index]
        for index in range(10)
    )

    check = 11 - (total % 11)

    if check == 10:
        check = 0
    elif check == 11:
        check = 1

    return check == int(digits[-1])


def _valid_date(value: str) -> str | None:
    accepted_formats = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
    )

    for format_string in accepted_formats:
        try:
            parsed = datetime.strptime(
                value.strip(),
                format_string,
            )
        except ValueError:
            continue

        if 2000 <= parsed.year <= datetime.now().year + 1:
            return parsed.date().isoformat()

    return None


def _decimal(value: str) -> Decimal | None:
    text = re.sub(
        r"[^0-9,.-]",
        "",
        value.strip(),
    )

    if not text:
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_sunat_payload(
    raw_text: str,
) -> tuple[dict[str, str], list[str]]:
    """Interpreta el formato SUNAT separado por barras verticales."""
    warnings: list[str] = []

    parts = [
        part.strip()
        for part in raw_text.strip().split("|")
    ]

    if len(parts) < 7:
        return {}, [
            "El QR no tiene el formato estructurado esperado."
        ]

    ruc = re.sub(r"\D", "", parts[0])
    document_type = parts[1]
    series = parts[2].upper().replace(" ", "")
    number = re.sub(r"\s+", "", parts[3])
    igv = _decimal(parts[4])
    total = _decimal(parts[5])
    date = _valid_date(parts[6])

    if not validate_peruvian_ruc(ruc):
        warnings.append(
            "El RUC del QR no supera el dígito verificador."
        )

    valid_series = re.fullmatch(
        r"[A-Z0-9]{1,6}",
        series,
    )
    valid_number = re.fullmatch(
        r"\d{1,12}",
        number,
    )

    if not valid_series or not valid_number:
        warnings.append(
            "La serie o el número del QR no tiene formato válido."
        )

    if date is None:
        warnings.append(
            "La fecha del QR no es válida."
        )

    if total is None or total <= 0:
        warnings.append(
            "El total del QR no es válido."
        )

    if igv is None or igv < 0:
        warnings.append(
            "El IGV del QR no es válido."
        )

    if (
        total is not None
        and igv is not None
        and igv > total
    ):
        warnings.append(
            "El IGV del QR es mayor que el total."
        )

    if warnings:
        return {}, warnings

    fields = {
        "ruc_emisor": ruc,
        "tipo_comprobante_codigo": document_type,
        "serie_numero": f"{series}-{number}",
        "igv": format(igv, "f"),
        "monto_total": format(total, "f"),
        "fecha": date or "",
    }

    if len(parts) >= 9:
        client_document = re.sub(
            r"\D",
            "",
            parts[8],
        )

        if client_document:
            fields["documento_cliente"] = client_document

    return fields, []


def _points_to_list(
    points: np.ndarray | None,
) -> list[list[float]]:
    if points is None:
        return []

    array = np.asarray(
        points,
        dtype=float,
    ).reshape(-1, 2)

    return [
        [
            round(float(x), 2),
            round(float(y), 2),
        ]
        for x, y in array
    ]


def _decode_candidates(
    image: np.ndarray,
) -> list[tuple[str, list[list[float]]]]:
    detector = cv2.QRCodeDetector()
    candidates: list[tuple[str, list[list[float]]]] = []

    try:
        found, decoded, points, _ = (
            detector.detectAndDecodeMulti(image)
        )

        if found and decoded:
            point_sets = (
                np.asarray(points)
                if points is not None
                else []
            )

            for index, text in enumerate(decoded):
                if not text:
                    continue

                candidate_points = (
                    point_sets[index]
                    if len(point_sets) > index
                    else None
                )

                candidates.append(
                    (
                        str(text),
                        _points_to_list(candidate_points),
                    )
                )
    except (cv2.error, ValueError):
        pass

    if candidates:
        return candidates

    try:
        text, points, _ = detector.detectAndDecode(image)

        if text:
            candidates.append(
                (
                    str(text),
                    _points_to_list(points),
                )
            )
    except cv2.error:
        pass

    return candidates


def read_qr(
    image: np.ndarray,
    source_image: str = "ORIGINAL",
) -> QRResult:
    """Detecta QR y solo acepta campos que superan todas las validaciones."""
    candidates = _decode_candidates(image)

    if not candidates:
        return QRResult(
            source_image=source_image,
        )

    collected_warnings: list[str] = []

    for raw_text, coordinates in candidates:
        fields, warnings = parse_sunat_payload(raw_text)

        if fields:
            return QRResult(
                detected=True,
                valid=True,
                raw_text=raw_text,
                fields=fields,
                warnings=[],
                coordinates=coordinates,
                source_image=source_image,
            )

        collected_warnings.extend(warnings)

    raw_text, coordinates = candidates[0]

    return QRResult(
        detected=True,
        valid=False,
        raw_text=raw_text,
        fields={},
        warnings=list(
            dict.fromkeys(collected_warnings)
        ),
        coordinates=coordinates,
        source_image=source_image,
    )
