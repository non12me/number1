"""Recuperación manual y limitada de campos mediante Gemini.

El flujo nunca envía la imagen ni el OCR completo. Solo transmite líneas
relevantes y únicamente propone valores para campos elegidos por el usuario.
"""

from __future__ import annotations

import json
import random
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from config import (
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_RELEVANT_TEXT_MAX_CHARS,
    GEMINI_RETRY_ATTEMPTS,
)
from models import JobState, utc_now_iso
from review import field_names
from utils import decode_json_from_sheet, encode_json_for_sheet


FIELD_KEYWORDS = {
    "concesionaria": ("concesion", "empresa", "razon social"),
    "lugar": ("lugar", "ubicacion", "via", "carretera"),
    "estacion": ("estacion", "peaje", "unidad"),
    "fecha": ("fecha", "/", "-"),
    "hora": ("hora", ":"),
    "placa": ("placa", "patente", "vehiculo"),
    "serie_numero": ("serie", "numero", "nro", "comprobante"),
    "ruc_emisor": ("ruc",),
    "razon_social": ("razon social", "empresa", "s.a.", "sac", "eirl"),
    "documento_cliente": ("dni", "documento", "cliente"),
    "concepto_resumen": ("descripcion", "concepto", "detalle", "producto"),
    "items_json": ("cantidad", "descripcion", "precio", "importe", "total"),
    "subtotal": ("subtotal", "sub total", "op. gravada", "base imponible"),
    "igv": ("igv", "impuesto"),
    "monto_total": ("total", "importe", "pagar"),
    "moneda": ("pen", "soles", "s/", "usd", "$"),
}


@dataclass(slots=True)
class GeminiResult:
    status: str
    fields: dict[str, str] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    warnings: list[str] = field(default_factory=list)


def _ocr_lines(payload: dict[str, Any]) -> list[str]:
    return [
        re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
        for page in payload.get("pages", [])
        for line in page.get("lines", [])
        if str(line.get("text", "")).strip()
    ]


def collect_relevant_lines(
    payload: dict[str, Any],
    requested_fields: list[str],
    max_chars: int = GEMINI_RELEVANT_TEXT_MAX_CHARS,
) -> list[str]:
    """Selecciona líneas etiquetadas y vecinas sin enviar el documento completo."""
    lines = _ocr_lines(payload)
    if not lines or max_chars <= 0:
        return []
    keywords = {
        keyword.casefold()
        for name in requested_fields
        for keyword in FIELD_KEYWORDS.get(name, (name.replace("_", " "),))
    }
    selected_indexes: set[int] = set()
    for index, line in enumerate(lines):
        normalized = line.casefold()
        if any(keyword in normalized for keyword in keywords):
            selected_indexes.update(
                position
                for position in (index - 1, index, index + 1)
                if 0 <= position < len(lines)
            )
    if not selected_indexes:
        selected_indexes.update(range(min(12, len(lines))))
        selected_indexes.update(range(max(0, len(lines) - 12), len(lines)))

    selected: list[str] = []
    used = 0
    for index in sorted(selected_indexes):
        line = lines[index]
        remaining = max_chars - used
        if remaining <= 0:
            break
        piece = line[:remaining]
        if piece:
            selected.append(piece)
            used += len(piece) + 1
    return selected


def _usage_count(metadata: Any, name: str) -> int:
    value = getattr(metadata, name, 0) if metadata is not None else 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def request_missing_fields(
    api_key: str,
    model: str,
    document_type: str,
    requested_fields: list[str],
    payload: dict[str, Any],
    validated_values: dict[str, str] | None = None,
    max_output_tokens: int = GEMINI_MAX_OUTPUT_TOKENS,
    client_factory: Callable[[str], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> GeminiResult:
    """Solicita JSON estricto; no modifica ni confirma datos automáticamente."""
    requested = list(dict.fromkeys(field for field in requested_fields if field))
    if not api_key:
        return GeminiResult(status="SIN_API_KEY", warnings=["Falta GEMINI_API_KEY."])
    if not requested:
        return GeminiResult(status="SIN_CAMPOS", warnings=["No se seleccionaron campos."])
    relevant_lines = collect_relevant_lines(payload, requested)
    if not relevant_lines:
        return GeminiResult(status="SIN_TEXTO", warnings=["No hay texto OCR relevante."])

    if client_factory is None:
        from google import genai

        client_factory = lambda key: genai.Client(api_key=key)

    schema = {
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "properties": {name: {"type": "string"} for name in requested},
                "required": requested,
                "additionalProperties": False,
            }
        },
        "required": ["fields"],
        "additionalProperties": False,
    }
    known = {
        key: value
        for key, value in (validated_values or {}).items()
        if value and key not in requested
    }
    prompt = (
        "Extrae únicamente los campos solicitados del texto OCR de un comprobante peruano. "
        "No inventes valores: usa una cadena vacía si no existe evidencia literal. "
        "No cambies los valores ya validados. Devuelve solo el JSON del esquema.\n"
        f"Tipo: {document_type}\n"
        f"Campos solicitados: {json.dumps(requested, ensure_ascii=False)}\n"
        f"Valores ya validados: {json.dumps(known, ensure_ascii=False)}\n"
        "Líneas OCR relevantes:\n" + "\n".join(relevant_lines)
    )

    last_error = ""
    for attempt in range(GEMINI_RETRY_ATTEMPTS + 1):
        try:
            client = client_factory(api_key)
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": schema,
                    "temperature": 0,
                    "max_output_tokens": max(32, min(int(max_output_tokens), 512)),
                },
            )
            parsed = getattr(response, "parsed", None)
            if parsed is None:
                parsed = json.loads(str(getattr(response, "text", "") or "{}"))
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump()
            raw_fields = parsed.get("fields", {}) if isinstance(parsed, dict) else {}
            fields = {
                name: re.sub(r"\s+", " ", str(raw_fields.get(name, ""))).strip()
                for name in requested
                if str(raw_fields.get(name, "")).strip()
            }
            usage = getattr(response, "usage_metadata", None)
            return GeminiResult(
                status="PROPUESTO",
                fields=fields,
                input_tokens=_usage_count(usage, "prompt_token_count"),
                output_tokens=_usage_count(usage, "candidates_token_count"),
                model=model,
                warnings=[] if fields else ["Gemini no encontró evidencia suficiente."],
            )
        except Exception as exc:  # noqa: BLE001
            last_error = type(exc).__name__
            transient = any(code in str(exc) for code in ("429", "500", "502", "503", "504"))
            if attempt >= GEMINI_RETRY_ATTEMPTS or not transient:
                break
            sleep(min(2.0, (2**attempt) + random.uniform(0.0, 0.25)))
    return GeminiResult(
        status="ERROR",
        model=model,
        warnings=[f"Gemini no respondió correctamente: {last_error or 'Error'}"],
    )


def merge_gemini_fields(
    payload: dict[str, Any],
    suggestions: dict[str, str],
    requested_fields: list[str],
) -> dict[str, Any]:
    """Incorpora sugerencias trazables sin declararlas válidas ni confirmadas."""
    merged = deepcopy(payload)
    extraction = merged.setdefault("extraction", {})
    fields = extraction.setdefault("fields", {})
    allowed = set(requested_fields)
    for name, value in suggestions.items():
        clean = re.sub(r"\s+", " ", str(value)).strip()
        if name not in allowed or not clean:
            continue
        current = dict(fields.get(name) or {})
        current.update(
            {
                "value": clean,
                "confidence_final": 0.70,
                "confidence_rule": 0.0,
                "source": "GEMINI",
                "warnings": ["Sugerencia de Gemini pendiente de revisión humana."],
                "raw_text": clean,
            }
        )
        fields[name] = current
    extraction["valid"] = False
    extraction["global_confidence"] = min(
        float(extraction.get("global_confidence", 0) or 0), 0.70
    )
    warnings = list(extraction.get("warnings") or [])
    warnings.append("Existen sugerencias de Gemini pendientes de revisión humana.")
    extraction["warnings"] = list(dict.fromkeys(warnings))
    return merged


def _today_gemini_requests(logs: list[dict[str, str]]) -> int:
    today = datetime.now(timezone.utc).date()
    count = 0
    for record in logs:
        if record.get("accion") != "GEMINI_PROPUESTO":
            continue
        try:
            created = datetime.fromisoformat(record.get("created_at", "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.date() == today:
            count += 1
    return count


def recover_job_with_gemini(
    credentials: Any,
    spreadsheet_id: str,
    job_id: str,
    api_key: str,
    model: str,
    requested_fields: list[str],
    user_email: str,
    daily_limit: int,
    max_output_tokens: int,
    client_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Ejecuta el fallback manual y guarda un checkpoint auditable."""
    from google_sheets import append_records, read_multiple_records, update_queue_jobs

    data = read_multiple_records(credentials, spreadsheet_id, ["OCR_COLA", "LOGS"])
    record = next(
        (row for row in data["OCR_COLA"] if row.get("job_id") == job_id), None
    )
    if record is None:
        return {"status": "NO_ENCONTRADO", "message": "El job ya no existe."}
    if record.get("estado") not in {
        JobState.EXTRAIDO_LOCAL.value,
        JobState.NECESITA_REVISION.value,
        JobState.PENDIENTE_RESULTADO.value,
    }:
        return {"status": "ESTADO_CAMBIO", "message": "El job cambió de estado."}
    if _today_gemini_requests(data["LOGS"]) >= max(0, daily_limit):
        return {"status": "LIMITE_DIARIO", "message": "Se alcanzó el límite diario configurado."}

    payload = decode_json_from_sheet(record.get("datos_extraidos_json", ""))
    valid_fields = set(field_names(record.get("tipo_documento", "")))
    requested = [name for name in requested_fields if name in valid_fields]
    current_fields = (payload.get("extraction") or {}).get("fields") or {}
    known = {
        name: str((metadata or {}).get("value") or "")
        for name, metadata in current_fields.items()
    }
    result = request_missing_fields(
        api_key=api_key,
        model=model,
        document_type=record.get("tipo_documento", ""),
        requested_fields=requested,
        payload=payload,
        validated_values=known,
        max_output_tokens=max_output_tokens,
        client_factory=client_factory,
    )
    if result.status != "PROPUESTO":
        return {
            "status": result.status,
            "message": " | ".join(result.warnings) or "No se obtuvieron propuestas.",
        }

    merged = merge_gemini_fields(payload, result.fields, requested)
    input_total = int(record.get("gemini_input_tokens") or 0) + result.input_tokens
    output_total = int(record.get("gemini_output_tokens") or 0) + result.output_tokens
    update_queue_jobs(
        credentials,
        spreadsheet_id,
        {
            job_id: {
                "estado": JobState.NECESITA_REVISION.value,
                "datos_extraidos_json": encode_json_for_sheet(merged),
                "gemini_usado": "TRUE",
                "gemini_input_tokens": input_total,
                "gemini_output_tokens": output_total,
                "updated_at": utc_now_iso(),
            }
        },
    )
    append_records(
        credentials,
        spreadsheet_id,
        "LOGS",
        [{
            "log_id": str(uuid4()),
            "accion": "GEMINI_PROPUESTO",
            "job_id": job_id,
            "sha256": record.get("sha256", ""),
            "detalle": json.dumps(
                {
                    "model": result.model,
                    "fields": sorted(result.fields),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "usuario": user_email,
            "created_at": utc_now_iso(),
        }],
    )
    return {
        "status": "PROPUESTO",
        "message": "Gemini propuso valores; compáralos con el original antes de confirmar.",
        "fields": result.fields,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }
