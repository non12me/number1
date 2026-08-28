"""Pruebas de validación QR sin llamadas externas."""

from __future__ import annotations

from qr_reader import parse_sunat_payload, validate_peruvian_ruc


def test_peruvian_ruc_check_digit() -> None:
    assert validate_peruvian_ruc("20100070970")
    assert not validate_peruvian_ruc("20100070971")


def test_valid_sunat_qr_payload() -> None:
    fields, warnings = parse_sunat_payload(
        "20100070970|01|F001|00001234|18.00|118.00|28/08/2026|6|12345678|HASH"
    )
    assert warnings == []
    assert fields["ruc_emisor"] == "20100070970"
    assert fields["serie_numero"] == "F001-00001234"
    assert fields["monto_total"] == "118.00"
    assert fields["fecha"] == "2026-08-28"


def test_invalid_qr_is_not_accepted() -> None:
    fields, warnings = parse_sunat_payload(
        "20100070971|01|F001|ABC|200.00|100.00|99/99/2026"
    )
    assert fields == {}
    assert warnings
