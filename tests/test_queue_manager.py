"""Pruebas de detección de duplicados sin Google ni credenciales."""

from __future__ import annotations

from queue_manager import find_exact_duplicates, find_visual_duplicates


def test_exact_duplicate_ignores_error_rows() -> None:
    records = [
        {"sha256": "igual", "estado": "ERROR"},
        {"sha256": "igual", "estado": "PENDIENTE"},
        {"sha256": "otro", "estado": "CONFIRMADO"},
    ]
    result = find_exact_duplicates(records, "igual")
    assert len(result) == 1
    assert result[0]["estado"] == "PENDIENTE"


def test_visual_duplicate_uses_hamming_distance() -> None:
    records = [
        {"job_id": "cercano", "perceptual_hash": "0000000000000001"},
        {"job_id": "lejano", "perceptual_hash": "ffffffffffffffff"},
    ]
    result = find_visual_duplicates(records, "0000000000000000")
    assert [record["job_id"] for record in result] == ["cercano"]


def test_pdf_without_visual_hash_has_no_match() -> None:
    assert find_visual_duplicates([], "") == []
