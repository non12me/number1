"""Trabajador secuencial con lock global y lease persistente por job."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from google.oauth2.credentials import Credentials

from config import OCR_LOCK_MINUTES
from document_processor import process_document_job
from google_sheets import append_records, read_records, update_queue_jobs
from models import JobState, utc_now_iso
from utils import encode_json_for_sheet


_GLOBAL_OCR_LOCK = threading.Lock()


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


def recover_expired_ocr_jobs(
    credentials: Credentials,
    spreadsheet_id: str,
) -> int:
    """Devuelve a PENDIENTE solamente leases de OCR realmente vencidos."""
    now = datetime.now(timezone.utc)
    records = read_records(credentials, spreadsheet_id, "OCR_COLA")
    updates: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("estado") != JobState.PROCESANDO.value:
            continue
        lock_until = _parse_datetime(record.get("lock_until", ""))
        if lock_until is not None and lock_until <= now:
            updates[record["job_id"]] = {
                "estado": JobState.PENDIENTE.value,
                "lock_owner": "",
                "lock_until": "",
                "error": "",
                "updated_at": utc_now_iso(),
            }
    return update_queue_jobs(credentials, spreadsheet_id, updates)


def retry_local_ocr_errors(
    credentials: Credentials,
    spreadsheet_id: str,
) -> int:
    """Reintenta solo la etapa OCR de jobs persistidos que fallaron en ella."""
    records = read_records(credentials, spreadsheet_id, "OCR_COLA")
    updates = {
        record["job_id"]: {
            "estado": JobState.PENDIENTE.value,
            "error": "",
            "lock_owner": "",
            "lock_until": "",
            "updated_at": utc_now_iso(),
        }
        for record in records
        if record.get("estado") == JobState.ERROR.value
        and record.get("error", "").startswith("OCR local:")
        and record.get("drive_file_id")
    }
    return update_queue_jobs(credentials, spreadsheet_id, updates)


def _claim_next_job(
    credentials: Credentials,
    spreadsheet_id: str,
    owner: str,
) -> dict[str, str] | None:
    records = read_records(credentials, spreadsheet_id, "OCR_COLA")
    pending = next(
        (record for record in records if record.get("estado") == JobState.PENDIENTE.value),
        None,
    )
    if pending is None:
        return None
    lock_until = datetime.now(timezone.utc) + timedelta(minutes=OCR_LOCK_MINUTES)
    attempts = int(pending.get("intentos") or 0) + 1
    updated = update_queue_jobs(
        credentials,
        spreadsheet_id,
        {
            pending["job_id"]: {
                "estado": JobState.PROCESANDO.value,
                "lock_owner": owner,
                "lock_until": lock_until.isoformat(timespec="seconds"),
                "intentos": attempts,
                "error": "",
                "updated_at": utc_now_iso(),
            }
        },
    )
    if updated != 1:
        return None
    verified = read_records(credentials, spreadsheet_id, "OCR_COLA")
    return next(
        (
            record
            for record in verified
            if record.get("job_id") == pending["job_id"]
            and record.get("estado") == JobState.PROCESANDO.value
            and record.get("lock_owner") == owner
        ),
        None,
    )


def _still_owned(
    credentials: Credentials,
    spreadsheet_id: str,
    job_id: str,
    owner: str,
) -> bool:
    return any(
        record.get("job_id") == job_id
        and record.get("estado") == JobState.PROCESANDO.value
        and record.get("lock_owner") == owner
        for record in read_records(credentials, spreadsheet_id, "OCR_COLA")
    )


def process_one_pending_job(
    credentials: Credentials,
    spreadsheet_id: str,
    owner: str,
    user_email: str,
) -> dict[str, Any]:
    """Procesa exactamente un documento y guarda su checkpoint antes de seguir."""
    if not _GLOBAL_OCR_LOCK.acquire(blocking=False):
        return {"status": "OCUPADO", "message": "Otro OCR está procesando un documento."}
    job: dict[str, str] | None = None
    try:
        recover_expired_ocr_jobs(credentials, spreadsheet_id)
        job = _claim_next_job(credentials, spreadsheet_id, owner)
        if job is None:
            return {"status": "SIN_PENDIENTES", "message": "No hay jobs PENDIENTES."}

        payload, final_state = process_document_job(credentials, job)
        if not _still_owned(credentials, spreadsheet_id, job["job_id"], owner):
            return {
                "status": "LOCK_PERDIDO",
                "job_id": job["job_id"],
                "message": "El lease cambió antes de guardar el resultado.",
            }
        payload_data = payload.model_dump(mode="json")
        extraction_confidence = {}
        if payload.extraction is not None:
            extraction_confidence = {
                "global_confidence": payload.extraction.global_confidence,
                "valid": payload.extraction.valid,
                "fields": {
                    name: {
                        "value": result.value,
                        "confidence_ocr": result.confidence_ocr,
                        "confidence_rule": result.confidence_rule,
                        "confidence_final": result.confidence_final,
                        "source": result.source,
                        "warnings": result.warnings,
                        "coordinates": result.coordinates,
                    }
                    for name, result in payload.extraction.fields.items()
                },
            }
        update_queue_jobs(
            credentials,
            spreadsheet_id,
            {
                job["job_id"]: {
                    "estado": final_state.value,
                    "datos_extraidos_json": encode_json_for_sheet(payload_data),
                    "confianza_json": json.dumps(
                        {
                            "line_count": payload.line_count,
                            "mean_ocr_confidence": payload.mean_ocr_confidence,
                            "weakest_quality": payload.weakest_quality,
                            "engine_profile": payload.engine_profile,
                            "extraction": extraction_confidence,
                        },
                        ensure_ascii=False,
                    ),
                    "advertencias_json": json.dumps(
                        payload.warnings,
                        ensure_ascii=False,
                    ),
                    "error": "",
                    "lock_owner": "",
                    "lock_until": "",
                    "updated_at": utc_now_iso(),
                }
            },
        )
        try:
            append_records(
                credentials,
                spreadsheet_id,
                "LOGS",
                [
                    {
                        "log_id": str(uuid4()),
                        "accion": "OCR_LOCAL_COMPLETADO",
                        "job_id": job["job_id"],
                        "sha256": job.get("sha256", ""),
                        "detalle": json.dumps(
                            {
                                "estado": final_state.value,
                                "lineas": payload.line_count,
                                "perfil": payload.engine_profile,
                                "confianza_global": (
                                    payload.extraction.global_confidence
                                    if payload.extraction is not None
                                    else None
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        "usuario": user_email,
                        "created_at": utc_now_iso(),
                    }
                ],
            )
        except Exception:
            pass
        return {
            "status": final_state.value,
            "job_id": job["job_id"],
            "filename": job.get("nombre_original", ""),
            "line_count": payload.line_count,
            "mean_confidence": payload.mean_ocr_confidence,
            "quality": payload.weakest_quality,
            "engine_profile": payload.engine_profile,
            "global_confidence": (
                payload.extraction.global_confidence
                if payload.extraction is not None
                else None
            ),
        }
    except Exception as exc:  # noqa: BLE001
        if job is not None and _still_owned(
            credentials,
            spreadsheet_id,
            job["job_id"],
            owner,
        ):
            update_queue_jobs(
                credentials,
                spreadsheet_id,
                {
                    job["job_id"]: {
                        "estado": JobState.ERROR.value,
                        "error": f"OCR local: {type(exc).__name__}",
                        "lock_owner": "",
                        "lock_until": "",
                        "updated_at": utc_now_iso(),
                    }
                },
            )
        return {
            "status": "ERROR",
            "job_id": job.get("job_id", "") if job else "",
            "filename": job.get("nombre_original", "") if job else "",
            "message": f"No se completó el OCR local: {type(exc).__name__}.",
        }
    finally:
        _GLOBAL_OCR_LOCK.release()
