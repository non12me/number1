"""Pruebas de formatos y reglas peruanas de la Fase 5."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from validators import (
    monetary_validation,
    normalize_date,
    normalize_decimal,
    normalize_plate,
    normalize_series_number,
    normalize_time,
    validate_dni,
    validate_peruvian_ruc,
)


def test_dates_accept_requested_formats_and_reject_impossible() -> None:
    now = datetime(2026, 8, 29)
    assert normalize_date("28/08/26", now) == "2026-08-28"
    assert normalize_date("28.08.2026", now) == "2026-08-28"
    assert normalize_date("28-08-2026", now) == "2026-08-28"
    assert normalize_date("31/02/2026", now) is None


def test_decimal_uses_last_separator_as_decimal_marker() -> None:
    assert normalize_decimal("S/ 1,234.56") == Decimal("1234.56")
    assert normalize_decimal("S/ 1.234,56") == Decimal("1234.56")
    assert normalize_decimal("11,80") == Decimal("11.80")


def test_plate_corrections_are_positional_only() -> None:
    assert normalize_plate("A1B-234") == "A1B-234"
    assert normalize_plate("A1B-23S") == "A1B-235"
    assert normalize_plate("ABC-XYZ") is None


def test_series_preserves_leading_zeroes() -> None:
    assert normalize_series_number("F001 - 00001234") == "F001-00001234"


def test_time_and_identity_validators() -> None:
    assert normalize_time("Hora 7:05") == "07:05"
    assert normalize_time("25:10") is None
    assert validate_dni("12345678")
    assert not validate_dni("1234567")
    assert validate_peruvian_ruc("20100070970")
    assert not validate_peruvian_ruc("20100070971")


def test_monetary_consistency_blocks_invalid_values() -> None:
    valid, warnings = monetary_validation(
        Decimal("10.00"),
        Decimal("1.80"),
        Decimal("11.80"),
    )
    assert valid
    assert warnings == []
    invalid, warnings = monetary_validation(
        Decimal("10.00"),
        Decimal("5.00"),
        Decimal("11.80"),
    )
    assert not invalid
    assert warnings
