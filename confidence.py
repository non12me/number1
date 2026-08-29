"""Puntuación, selección conservadora y confianza global por documento."""

from __future__ import annotations

from collections.abc import Iterable

from config import (
    CANDIDATE_CONFLICT_DELTA,
    CANDIDATE_WEIGHTS,
    CONFIDENCE_REVIEW,
)
from models import FieldCandidate, FieldResult, ValueSource


def score_candidate(
    ocr_confidence: float,
    format_score: float,
    label_score: float,
    position_score: float,
    consistency_score: float,
) -> float:
    components = {
        "ocr": ocr_confidence,
        "format": format_score,
        "label": label_score,
        "position": position_score,
        "consistency": consistency_score,
    }
    score = sum(
        max(0.0, min(1.0, components[name])) * weight
        for name, weight in CANDIDATE_WEIGHTS.items()
    )
    return round(max(0.0, min(1.0, score)), 4)


def _unique_candidates(candidates: Iterable[FieldCandidate]) -> list[FieldCandidate]:
    best_by_value: dict[str, FieldCandidate] = {}
    for candidate in candidates:
        current = best_by_value.get(candidate.value)
        if current is None or candidate.final_score > current.final_score:
            best_by_value[candidate.value] = candidate
    return sorted(
        best_by_value.values(),
        key=lambda item: (item.final_score, item.ocr_confidence),
        reverse=True,
    )


def select_field_result(
    candidates: Iterable[FieldCandidate],
    threshold: float = CONFIDENCE_REVIEW,
) -> FieldResult:
    ranked = _unique_candidates(candidates)
    if not ranked:
        return FieldResult(
            value=None,
            warnings=["No se encontraron candidatos."],
        )

    best = ranked[0]
    warnings = list(best.warnings)
    conflict = (
        len(ranked) > 1
        and ranked[1].value != best.value
        and best.final_score - ranked[1].final_score <= CANDIDATE_CONFLICT_DELTA
    )
    accepted = best.final_score >= threshold and not conflict
    if best.final_score < threshold:
        warnings.append("El mejor candidato no supera el umbral de revisión.")
    if conflict:
        warnings.append("Existen dos candidatos con puntuaciones similares.")

    rule_weight = sum(
        CANDIDATE_WEIGHTS[name]
        for name in ("format", "label", "position", "consistency")
    )
    rule_score = (
        best.format_score * CANDIDATE_WEIGHTS["format"]
        + best.label_score * CANDIDATE_WEIGHTS["label"]
        + best.position_score * CANDIDATE_WEIGHTS["position"]
        + best.consistency_score * CANDIDATE_WEIGHTS["consistency"]
    ) / rule_weight

    return FieldResult(
        value=best.value if accepted else None,
        confidence_ocr=best.ocr_confidence,
        confidence_rule=round(rule_score, 4),
        confidence_final=best.final_score,
        source=best.source,
        warnings=list(dict.fromkeys(warnings)),
        coordinates=best.coordinates,
        page=best.page,
        raw_text=best.raw_text,
        candidates=ranked[:5],
    )


def calculated_field(value: str, confidence: float, warning: str) -> FieldResult:
    return FieldResult(
        value=value,
        confidence_ocr=0.0,
        confidence_rule=confidence,
        confidence_final=confidence,
        source=ValueSource.CALCULADO,
        warnings=[warning],
    )


def global_confidence(
    fields: dict[str, FieldResult],
    required_fields: Iterable[str],
) -> float:
    """La confianza queda limitada por el obligatorio más débil."""
    scores = [
        fields[name].confidence_final
        if name in fields and fields[name].value is not None
        else 0.0
        for name in required_fields
    ]
    return round(min(scores), 4) if scores else 0.0
