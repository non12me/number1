"""Pruebas de selección conservadora y confianza global."""

from __future__ import annotations

from confidence import global_confidence, select_field_result
from models import FieldCandidate, FieldResult


def make_candidate(value: str, score: float, ocr: float = 0.90) -> FieldCandidate:
    return FieldCandidate(
        field="placa",
        raw_text=value,
        value=value,
        ocr_confidence=ocr,
        format_score=1.0,
        label_score=1.0,
        position_score=1.0,
        consistency_score=1.0,
        final_score=score,
    )


def test_selection_uses_score_not_first_match() -> None:
    result = select_field_result(
        [make_candidate("ABC-123", 0.80), make_candidate("A1B-234", 0.96)]
    )
    assert result.value == "A1B-234"
    assert result.confidence_final == 0.96


def test_similar_candidates_create_conflict() -> None:
    result = select_field_result(
        [make_candidate("ABC-123", 0.91), make_candidate("A1B-234", 0.89)]
    )
    assert result.value is None
    assert any("similares" in warning for warning in result.warnings)


def test_global_confidence_is_weakest_required_field() -> None:
    fields = {
        "fecha": FieldResult(value="2026-08-28", confidence_final=0.98),
        "placa": FieldResult(value="A1B-234", confidence_final=0.91),
        "total": FieldResult(value="11.80", confidence_final=0.95),
    }
    assert global_confidence(fields, ("fecha", "placa", "total")) == 0.91
    assert global_confidence(fields, ("fecha", "lugar")) == 0.0
