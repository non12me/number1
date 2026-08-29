"""Normalización y validaciones deterministas para documentos peruanos."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from config import (
    MONETARY_TOLERANCE,
    PERUVIAN_PLATE_MASKS,
    REASONABLE_MAX_FUTURE_YEARS,
    REASONABLE_MIN_YEAR,
)


DATE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})(?!\d)"
)
TIME_PATTERN = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?::([0-5]\d))?(?!\d)")
SERIES_PATTERN = re.compile(
    r"(?<![A-Z0-9])([FBE][A-Z0-9]{2,4})\s*[-–—]\s*(\d{1,12})(?!\d)",
    re.IGNORECASE,
)


def normalize_date(value: str, now: datetime | None = None) -> str | None:
    """Acepta formatos peruanos y devuelve YYYY-MM-DD si la fecha es real."""
    match = DATE_PATTERN.search(value.strip())
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000
    reference = now or datetime.now()
    if not REASONABLE_MIN_YEAR <= year <= reference.year + REASONABLE_MAX_FUTURE_YEARS:
        return None
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def normalize_time(value: str) -> str | None:
    match = TIME_PATTERN.search(value.strip())
    if not match:
        return None
    hour, minute, second = match.groups()
    normalized = f"{int(hour):02d}:{int(minute):02d}"
    if second is not None:
        normalized += f":{int(second):02d}"
    return normalized


def normalize_decimal(value: str) -> Decimal | None:
    """Interpreta separadores peruanos usando Decimal, nunca float."""
    text = value.strip().upper()
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = re.sub(r"[^0-9,.-]", "", text).replace("-", "")
    if not text or not re.search(r"\d", text):
        return None

    if "," in text and "." in text:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        text = text.replace(thousands_separator, "")
        text = text.replace(decimal_separator, ".")
    elif text.count(",") == 1:
        left, right = text.split(",")
        text = left + ("." + right if len(right) <= 2 else right)
    elif text.count(".") == 1:
        left, right = text.split(".")
        text = left + ("." + right if len(right) <= 2 else right)
    elif text.count(",") > 1:
        text = text.replace(",", "")
    elif text.count(".") > 1:
        parts = text.split(".")
        text = "".join(parts[:-1]) + ("." + parts[-1] if len(parts[-1]) <= 2 else parts[-1])

    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return -result if negative else result


def decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def normalize_series_number(value: str) -> str | None:
    match = SERIES_PATTERN.search(value.upper())
    if not match:
        return None
    return f"{match.group(1).upper()}-{match.group(2)}"


def _correct_for_mask(value: str, mask: str) -> tuple[str, int] | None:
    if len(value) != len(mask):
        return None
    letter_corrections = {"0": "O", "1": "I", "5": "S"}
    digit_corrections = {"O": "0", "I": "1", "S": "5"}
    output: list[str] = []
    corrections = 0
    for character, expected in zip(value, mask, strict=True):
        if expected == "L":
            corrected = character if character.isalpha() else letter_corrections.get(character)
        else:
            corrected = character if character.isdigit() else digit_corrections.get(character)
        if corrected is None:
            return None
        if corrected != character:
            corrections += 1
        output.append(corrected)
    return "".join(output), corrections


def normalize_plate_candidates(value: str) -> list[str]:
    """Corrige O/0, I/1 y S/5 solamente según la posición del patrón."""
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    scored_candidates = [
        corrected
        for mask in PERUVIAN_PLATE_MASKS
        if (corrected := _correct_for_mask(compact, mask)) is not None
    ]
    if not scored_candidates:
        return []
    minimum_corrections = min(item[1] for item in scored_candidates)
    return sorted(
        {
            corrected
            for corrected, correction_count in scored_candidates
            if correction_count == minimum_corrections
        }
    )


def normalize_plate(value: str) -> str | None:
    candidates = normalize_plate_candidates(value)
    if len(candidates) != 1:
        return None
    compact = candidates[0]
    return f"{compact[:3]}-{compact[3:]}"


def validate_dni(value: str) -> bool:
    return bool(re.fullmatch(r"\d{8}", re.sub(r"\D", "", value)))


def validate_peruvian_ruc(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    weights = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    total = sum(int(digit) * weight for digit, weight in zip(digits[:10], weights, strict=True))
    check = 11 - (total % 11)
    check = 0 if check == 10 else 1 if check == 11 else check
    return check == int(digits[-1])


def monetary_validation(
    subtotal: Decimal | None,
    igv: Decimal | None,
    total: Decimal | None,
    tolerance: Decimal = Decimal(MONETARY_TOLERANCE),
) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    if total is None:
        return False, ["Falta el monto total obligatorio."]
    if total <= 0:
        warnings.append("El total debe ser mayor que cero.")
    if igv is not None and igv < 0:
        warnings.append("El IGV no puede ser negativo.")
    if subtotal is not None and subtotal < 0:
        warnings.append("El subtotal no puede ser negativo.")
    if igv is not None and total < igv:
        warnings.append("El total es menor que el IGV.")
    if subtotal is not None and igv is not None:
        if abs((subtotal + igv) - total) > tolerance:
            warnings.append("Subtotal más IGV no coincide con el total.")
    return not warnings, warnings


def currency_from_text(value: str) -> str | None:
    upper = value.upper()
    if "USD" in upper or "US$" in upper or "$" in upper:
        return "USD"
    if "PEN" in upper or "S/" in upper or "SOLES" in upper or "SOL " in upper:
        return "PEN"
    return None
