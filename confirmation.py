"""Guardado, rechazo y confirmación idempotente de la revisión humana."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import OCR_LOCK_MINUTES
from models import DocumentType, JobState, utc_now_iso
from review import apply_review_values, field_names, values_from_payload
from utils import decode_json_from_sheet, encode_json_for_sheet


_CONFIRMATION_LOCK = threading.RLock()
REVIEWABLE_STATES = {
    JobState.EXTRAIDO_LOCAL.value,
    JobState.NECESITA_REVISION.value,
    JobState.PENDIENTE_RESULTADO.value,
}
RESULT_SHEETS = {
    DocumentType.PEAJE: "PEAJES",
    DocumentType.BOLETA: "BOLETAS",
    DocumentType.FACTURA: "FACTURAS",
}


def _read_records(credentials, spreadsheet_id: str, sheet_name: str):
    from google_sheets import read_records

    return read_records(credentials, spreadsheet_id, sheet_name)


def _append_records(credentials, spreadsheet_id: str, sheet_name: str, records):
    from google_sheets import append_records

    return append_records(credentials, spreadsheet_id, sheet_name, records)


def _update_queue_jobs(credentials, spreadsheet_id: str, updates):
    from google_sheets import update_queue_jobs

    return update_queue_jobs(credentials, spreadsheet_id, updates)


def _upsert_record(
    credentials,
    spreadsheet_id: str,
    sheet_name: str,
    key_column: str,
    key_value: str,
    record: dict[str, Any],
):
    from google_sheets import upsert_record

    return upsert_record(
        credentials, spreadsheet_id, sheet_name, key_column, key_value, record
    )


def _move_file_to_folder(credentials, file_id: str, folder_id: str, new_name: str):
    from google_drive import move_file_to_folder

    return move_file_to_folder(credentials, file_id, folder_id, new_name)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _job(records: list[dict[str, str]], job_id: str) -> dict[str, str] | None:
    return next((record for record in records if record.get("job_id") == job_id), None)


def _review_payload(record: dict[str, str]) -> dict[str, Any]:
    value = record.get("datos_extraidos_json", "")
    if not value:
        raise ValueError("El job no contiene un resultado OCR para revisar.")
    payload = decode_json_from_sheet(value)
    if not isinstance(payload, dict) or not payload.get("extraction"):
        raise ValueError("El resultado OCR no contiene extracción estructurada.")
    return payload


def _confidence_snapshot(payload: dict[str, Any]) -> str:
    extraction = payload.get("extraction") or {}
    fields = extraction.get("fields") or {}
    return json.dumps(
        {
            "human_reviewed": True,
            "global_confidence": extraction.get("global_confidence", 0),
            "valid": extraction.get("valid", False),
            "fields": {
                name: {
                    "value": data.get("value"),
                    "confidence_ocr": data.get("confidence_ocr", 0),
                    "confidence_rule": data.get("confidence_rule", 0),
                    "confidence_final": data.get("confidence_final", 0),
                    "source": data.get("source", "OCR"),
                    "warnings": data.get("warnings", []),
                    "coordinates": data.get("coordinates", []),
                }
                for name, data in fields.items()
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _append_log(
    credentials,
    spreadsheet_id: str,
    action: str,
    record: dict[str, str],
    user_email: str,
    detail: dict[str, Any],
) -> None:
    try:
        _append_records(
            credentials,
            spreadsheet_id,
            "LOGS",
            [{
                "log_id": str(uuid4()),
                "accion": action,
                "job_id": record.get("job_id", ""),
                "sha256": record.get("sha256", ""),
                "detalle": json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
                "usuario": user_email,
                "created_at": utc_now_iso(),
            }],
        )
    except Exception:
        pass


def _append_corrections_once(
    credentials,
    spreadsheet_id: str,
    job_id: str,
    corrections: list[dict[str, str]],
) -> int:
    if not corrections:
        return 0
    existing = _read_records(credentials, spreadsheet_id, "CORRECCIONES")
    keys = {
        (
            row.get("job_id", ""), row.get("campo", ""),
            row.get("valor_detectado", ""), row.get("valor_corregido", ""),
        )
        for row in existing
    }
    pending = []
    for correction in corrections:
        correction["job_id"] = job_id
        key = (
            job_id, correction.get("campo", ""),
            correction.get("valor_detectado", ""),
            correction.get("valor_corregido", ""),
        )
        if key in keys:
            continue
        keys.add(key)
        pending.append(correction)
    return _append_records(credentials, spreadsheet_id, "CORRECCIONES", pending)


def _safe_piece(value: str, fallback: str = "SIN_DATO") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().upper())
    text = re.sub(r"-+", "-", text).strip("-._")
    return (text or fallback)[:40]


def suggested_filename(
    record: dict[str, str],
    values: dict[str, str],
    shared_job_count: int = 1,
) -> str:
    """Construye un nombre estable sin rutas ni caracteres inseguros."""
    extension = Path(record.get("nombre_original", "")).suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png", ".webp", ".pdf"}:
        extension = ".pdf" if record.get("mime_type") == "application/pdf" else ".jpg"
    sha_piece = _safe_piece(
        record.get("sha256", "")[:8], record.get("job_id", "")[:8]
    )
    if shared_job_count > 1:
        return f"LOTE_CONFIRMADO_{sha_piece}{extension}"
    kind = _safe_piece(record.get("tipo_documento", "DOCUMENTO"))
    date = _safe_piece(values.get("fecha", "SIN_FECHA"))
    identifier = values.get("placa") or values.get("ruc_emisor") or "SIN_ID"
    series = values.get("serie_numero") or "SIN_SERIE"
    return (
        f"{kind}_{date}_{_safe_piece(identifier)}_"
        f"{_safe_piece(series)}_{sha_piece}{extension}"
    )


def _next_number(records: list[dict[str, str]], existing: dict[str, str] | None) -> int:
    if existing and str(existing.get("numero", "")).isdigit():
        return int(existing["numero"])
    numbers = [int(row["numero"]) for row in records if str(row.get("numero", "")).isdigit()]
    return max(numbers, default=0) + 1


def _result_record(
    job: dict[str, str],
    payload: dict[str, Any],
    final_name: str,
    user_email: str,
    confirmed_at: str,
    existing_rows: list[dict[str, str]],
) -> tuple[str, dict[str, Any]]:
    kind = DocumentType(job.get("tipo_documento", ""))
    sheet_name = RESULT_SHEETS[kind]
    values = values_from_payload(payload, kind)
    existing = next(
        (row for row in existing_rows if row.get("job_id") == job.get("job_id")), None
    )
    result: dict[str, Any] = {
        "numero": _next_number(existing_rows, existing),
        "job_id": job["job_id"],
        "nombre_archivo": final_name,
        "drive_file_id": job.get("drive_file_id", ""),
        "usuario": user_email,
        "confirmed_at": confirmed_at,
    }
    result.update({name: values.get(name, "") for name in field_names(kind)})
    return sheet_name, result


def save_review_draft(
    credentials,
    spreadsheet_id: str,
    job_id: str,
    values: dict[str, Any],
    user_email: str,
) -> dict[str, Any]:
    """Guarda correcciones sin confirmar ni mover el original."""
    with _CONFIRMATION_LOCK:
        current = _job(_read_records(credentials, spreadsheet_id, "OCR_COLA"), job_id)
        if current is None:
            return {"status": "NO_ENCONTRADO", "message": "El job ya no existe."}
        if current.get("estado") == JobState.CONFIRMADO.value:
            return {"status": "YA_CONFIRMADO", "message": "El job ya fue confirmado."}
        if current.get("estado") not in REVIEWABLE_STATES:
            return {
                "status": "ESTADO_INVALIDO",
                "message": f"No se puede editar un job en estado {current.get('estado', '')}.",
            }
        updated, validation, corrections = apply_review_values(
            _review_payload(current), current.get("tipo_documento", ""), values,
            user_email, "GUARDAR_BORRADOR",
        )
        _append_corrections_once(credentials, spreadsheet_id, job_id, corrections)
        _update_queue_jobs(credentials, spreadsheet_id, {job_id: {
            "estado": JobState.NECESITA_REVISION.value,
            "datos_extraidos_json": encode_json_for_sheet(updated),
            "confianza_json": _confidence_snapshot(updated),
            "advertencias_json": json.dumps(
                validation.errors + validation.warnings, ensure_ascii=False
            ),
            "error": "",
            "updated_at": utc_now_iso(),
        }})
        _append_log(
            credentials, spreadsheet_id, "REVISION_GUARDADA", current, user_email,
            {"correcciones": len(corrections), "valido": validation.valid},
        )
        return {
            "status": "GUARDADO",
            "message": "Las correcciones quedaron guardadas; el original sigue en OCR_ENTRADA.",
            "valid": validation.valid,
            "errors": validation.errors,
            "warnings": validation.warnings,
            "corrections": len(corrections),
        }


def reject_review(
    credentials,
    spreadsheet_id: str,
    job_id: str,
    reason: str,
    user_email: str,
) -> dict[str, Any]:
    """Rechaza la revisión y conserva el original en OCR_ENTRADA."""
    clean_reason = re.sub(r"\s+", " ", reason.strip())
    if len(clean_reason) < 3:
        return {"status": "MOTIVO_REQUERIDO", "message": "Escribe el motivo del rechazo."}
    with _CONFIRMATION_LOCK:
        current = _job(_read_records(credentials, spreadsheet_id, "OCR_COLA"), job_id)
        if current is None:
            return {"status": "NO_ENCONTRADO", "message": "El job ya no existe."}
        if current.get("estado") == JobState.CONFIRMADO.value:
            return {"status": "YA_CONFIRMADO", "message": "El job ya fue confirmado."}
        if current.get("estado") not in REVIEWABLE_STATES:
            return {
                "status": "ESTADO_INVALIDO",
                "message": f"No se puede rechazar un job en estado {current.get('estado', '')}.",
            }
        _update_queue_jobs(credentials, spreadsheet_id, {job_id: {
            "estado": JobState.NECESITA_REVISION.value,
            "error": f"Revisión rechazada: {clean_reason}",
            "lock_owner": "",
            "lock_until": "",
            "updated_at": utc_now_iso(),
        }})
        _append_log(
            credentials, spreadsheet_id, "REVISION_RECHAZADA", current, user_email,
            {"motivo": clean_reason},
        )
        return {
            "status": "RECHAZADO",
            "message": "Revisión rechazada. El original permanece en OCR_ENTRADA.",
        }


def _claim_confirmation(
    credentials,
    spreadsheet_id: str,
    job_id: str,
    owner: str,
) -> tuple[dict[str, str] | None, str]:
    current = _job(_read_records(credentials, spreadsheet_id, "OCR_COLA"), job_id)
    if current is None:
        return None, "NO_ENCONTRADO"
    state = current.get("estado", "")
    if state == JobState.CONFIRMADO.value:
        return current, "YA_CONFIRMADO"
    if state == JobState.CONFIRMANDO.value:
        lock_until = _parse_datetime(current.get("lock_until", ""))
        if lock_until is None or lock_until > datetime.now(timezone.utc):
            return current, "OCUPADO"
    elif state not in REVIEWABLE_STATES:
        return current, "ESTADO_INVALIDO"
    lock_until = datetime.now(timezone.utc) + timedelta(minutes=OCR_LOCK_MINUTES)
    updated = _update_queue_jobs(credentials, spreadsheet_id, {job_id: {
        "estado": JobState.CONFIRMANDO.value,
        "lock_owner": owner,
        "lock_until": lock_until.isoformat(timespec="seconds"),
        "error": "",
        "updated_at": utc_now_iso(),
    }})
    if updated != 1:
        return current, "LOCK_PERDIDO"
    verified = _job(_read_records(credentials, spreadsheet_id, "OCR_COLA"), job_id)
    if (
        verified is None
        or verified.get("estado") != JobState.CONFIRMANDO.value
        or verified.get("lock_owner") != owner
    ):
        return verified, "LOCK_PERDIDO"
    return verified, "RECLAMADO"


def confirm_review(
    credentials,
    spreadsheet_id: str,
    confirmed_folder_id: str,
    job_id: str,
    values: dict[str, Any],
    user_email: str,
) -> dict[str, Any]:
    """Confirma una vez, registra el resultado y mueve el mismo archivo."""
    owner = f"CONFIRM-{uuid4()}"
    with _CONFIRMATION_LOCK:
        initial = _job(_read_records(credentials, spreadsheet_id, "OCR_COLA"), job_id)
        if initial is None:
            return {"status": "NO_ENCONTRADO", "message": "El job ya no existe."}
        if initial.get("estado") == JobState.CONFIRMADO.value:
            return {
                "status": "YA_CONFIRMADO",
                "message": "La confirmación ya estaba completa; no se duplicó ninguna fila.",
            }
        updated_payload, validation, corrections = apply_review_values(
            _review_payload(initial), initial.get("tipo_documento", ""), values,
            user_email, "CONFIRMAR",
        )
        if not validation.valid:
            return {
                "status": "VALIDACION_ERROR",
                "message": "Corrige los campos bloqueantes antes de confirmar.",
                "errors": validation.errors,
                "warnings": validation.warnings,
            }

        claimed, claim_status = _claim_confirmation(
            credentials, spreadsheet_id, job_id, owner
        )
        if claim_status != "RECLAMADO" or claimed is None:
            messages = {
                "YA_CONFIRMADO": "La confirmación ya estaba completa; no se duplicó ninguna fila.",
                "OCUPADO": "Otro usuario está confirmando este documento.",
                "ESTADO_INVALIDO": "El documento cambió de estado y no puede confirmarse ahora.",
                "LOCK_PERDIDO": "Otro usuario obtuvo el lock de confirmación.",
                "NO_ENCONTRADO": "El job ya no existe.",
            }
            return {"status": claim_status, "message": messages.get(claim_status, claim_status)}

        previous_state = initial.get("estado", JobState.NECESITA_REVISION.value)
        try:
            _append_corrections_once(credentials, spreadsheet_id, job_id, corrections)
            all_jobs = _read_records(credentials, spreadsheet_id, "OCR_COLA")
            siblings = [
                row for row in all_jobs
                if row.get("drive_file_id") == claimed.get("drive_file_id")
            ]
            final_name = suggested_filename(
                claimed, validation.normalized_values, max(1, len(siblings))
            )
            sheet_name = RESULT_SHEETS[DocumentType(claimed.get("tipo_documento", ""))]
            existing_results = _read_records(credentials, spreadsheet_id, sheet_name)
            confirmed_at = utc_now_iso()
            _, result = _result_record(
                claimed, updated_payload, final_name, user_email,
                confirmed_at, existing_results,
            )
            upsert_status = _upsert_record(
                credentials, spreadsheet_id, sheet_name, "job_id", job_id, result
            )

            other_siblings = [row for row in siblings if row.get("job_id") != job_id]
            move_ready = all(
                row.get("estado") == JobState.CONFIRMADO.value
                for row in other_siblings
            )
            moved = False
            if move_ready:
                _move_file_to_folder(
                    credentials, claimed.get("drive_file_id", ""),
                    confirmed_folder_id, final_name,
                )
                moved = True

            updated_count = _update_queue_jobs(credentials, spreadsheet_id, {job_id: {
                "estado": JobState.CONFIRMADO.value,
                "datos_extraidos_json": encode_json_for_sheet(updated_payload),
                "confianza_json": _confidence_snapshot(updated_payload),
                "advertencias_json": json.dumps(validation.warnings, ensure_ascii=False),
                "error": "",
                "lock_owner": "",
                "lock_until": "",
                "updated_at": confirmed_at,
                "confirmed_at": confirmed_at,
            }})
            if updated_count != 1:
                raise RuntimeError("No se guardó el checkpoint CONFIRMADO.")
            _append_log(
                credentials, spreadsheet_id, "DOCUMENTO_CONFIRMADO", claimed,
                user_email, {
                    "pestaña": sheet_name,
                    "resultado": upsert_status,
                    "archivo_movido": moved,
                    "nombre_final": final_name,
                    "jobs_mismo_archivo": len(siblings),
                },
            )
            message = "Documento confirmado y original movido a OCR_CONFIRMADOS."
            if not moved:
                message = (
                    "Job confirmado. El PDF permanece en OCR_ENTRADA hasta confirmar "
                    "sus demás páginas lógicas."
                )
            return {
                "status": "CONFIRMADO",
                "message": message,
                "sheet": sheet_name,
                "filename": final_name,
                "moved": moved,
                "result_write": upsert_status,
                "corrections": len(corrections),
            }
        except Exception as exc:  # noqa: BLE001
            try:
                _update_queue_jobs(credentials, spreadsheet_id, {job_id: {
                    "estado": (
                        previous_state
                        if previous_state in REVIEWABLE_STATES
                        else JobState.NECESITA_REVISION.value
                    ),
                    "error": f"Confirmación: {type(exc).__name__}",
                    "lock_owner": "",
                    "lock_until": "",
                    "updated_at": utc_now_iso(),
                }})
            except Exception:
                pass
            return {
                "status": "ERROR",
                "message": (
                    f"La confirmación quedó pendiente en la etapa {type(exc).__name__}. "
                    "Puedes reintentar sin duplicar el resultado."
                ),
            }
