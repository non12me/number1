"""Diccionarios y plantillas persistentes leídos desde Google Sheets."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from google.oauth2.credentials import Credentials
from rapidfuzz import fuzz

from config import DICTIONARY_MATCH_THRESHOLD
from google_sheets import read_records
from models import ExtractionResult, PageOCRResult


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.upper())
    text = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", text).strip()


def _active(value: str) -> bool:
    return str(value).strip().casefold() in {"true", "1", "si", "sí", "activo", "yes"}


@dataclass(frozen=True, slots=True)
class KnowledgeBase:
    dictionaries: list[dict[str, str]] = field(default_factory=list)
    templates: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    provider: str
    regions: dict[str, tuple[float, float, float, float]]
    identifying_words: tuple[str, ...]


def load_knowledge(
    credentials: Credentials,
    spreadsheet_id: str,
) -> KnowledgeBase:
    """Carga reglas activas; un fallo no bloquea el OCR local básico."""
    warnings: list[str] = []
    try:
        dictionaries = [
            row
            for row in read_records(credentials, spreadsheet_id, "DICCIONARIOS")
            if _active(row.get("activo", ""))
        ]
    except Exception as exc:  # noqa: BLE001
        dictionaries = []
        warnings.append(f"No se cargaron diccionarios: {type(exc).__name__}.")
    try:
        templates = [
            row
            for row in read_records(credentials, spreadsheet_id, "PLANTILLAS")
            if _active(row.get("activo", ""))
        ]
    except Exception as exc:  # noqa: BLE001
        templates = []
        warnings.append(f"No se cargaron plantillas: {type(exc).__name__}.")
    return KnowledgeBase(dictionaries=dictionaries, templates=templates, warnings=warnings)


def canonical_reason_social(
    ruc: str | None,
    detected: str | None,
    knowledge: KnowledgeBase,
) -> tuple[str | None, float, str]:
    """Aplica relaciones verificadas RUC–razón social y variantes activas."""
    if ruc:
        for row in knowledge.dictionaries:
            kind = _plain(row.get("tipo", ""))
            canonical = row.get("valor_canonico", "").strip()
            variant = row.get("variante", "").strip()
            provider = row.get("proveedor", "").strip()
            if kind in {"RUC_PROVEEDOR", "RUC_RAZON_SOCIAL"}:
                if re.sub(r"\D", "", canonical) == ruc and (provider or variant):
                    return provider or variant, 1.0, "Relación RUC–razón social verificada."
                if re.sub(r"\D", "", variant) == ruc and (canonical or provider):
                    return provider or canonical, 1.0, "Relación RUC–razón social verificada."

    if detected:
        best: tuple[float, str] | None = None
        for row in knowledge.dictionaries:
            if _plain(row.get("tipo", "")) not in {"RAZON_SOCIAL", "PROVEEDOR"}:
                continue
            canonical = row.get("valor_canonico", "").strip()
            variant = row.get("variante", "").strip() or canonical
            if not canonical:
                continue
            score = fuzz.ratio(_plain(detected), _plain(variant)) / 100.0
            if best is None or score > best[0]:
                best = (score, canonical)
        if best and best[0] >= DICTIONARY_MATCH_THRESHOLD:
            return best[1], best[0], "Razón social normalizada mediante diccionario verificado."
    return detected, 0.0, ""


def _identifying_words(value: str) -> tuple[str, ...]:
    text = value.strip()
    if not text:
        return ()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return tuple(_plain(str(item)) for item in parsed if str(item).strip())
    return tuple(_plain(item) for item in re.split(r"[|,;\n]+", text) if item.strip())


def _region(value: str) -> tuple[float, float, float, float] | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = [part.strip() for part in text.split(",")]
    if not isinstance(parsed, list) or len(parsed) != 4:
        return None
    try:
        coordinates = tuple(float(item) for item in parsed)
    except (TypeError, ValueError):
        return None
    x1, y1, x2, y2 = coordinates
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None
    return x1, y1, x2, y2


def match_template(
    extraction: ExtractionResult,
    pages: list[PageOCRResult],
    knowledge: KnowledgeBase,
) -> TemplateMatch | None:
    text = _plain("\n".join(line.text for page in pages for line in page.lines))
    reason = extraction.fields.get("razon_social")
    detected_provider = _plain(reason.value or "") if reason else ""
    best: tuple[float, dict[str, str], tuple[str, ...]] | None = None
    for row in knowledge.templates:
        provider = row.get("proveedor", "").strip()
        words = _identifying_words(row.get("palabras_identificadoras", ""))
        word_score = (
            sum(word in text for word in words) / len(words)
            if words
            else 0.0
        )
        provider_score = (
            fuzz.ratio(detected_provider, _plain(provider)) / 100.0
            if detected_provider and provider
            else 0.0
        )
        score = max(word_score, provider_score)
        if score >= 0.75 and (best is None or score > best[0]):
            best = (score, row, words)
    if best is None:
        return None

    row = best[1]
    mapping = {
        "ruc_emisor": "posicion_ruc",
        "fecha": "posicion_fecha",
        "placa": "posicion_placa",
        "monto_total": "posicion_total",
    }
    regions = {
        field_name: region
        for field_name, column in mapping.items()
        if (region := _region(row.get(column, ""))) is not None
    }
    return TemplateMatch(
        provider=row.get("proveedor", "").strip(),
        regions=regions,
        identifying_words=best[2],
    )
