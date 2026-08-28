"""Coordinación de carga, duplicados y checkpoints de OCR_COLA."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from uuid import uuid4

from google.oauth2.credentials import Credentials

from config import VISUAL_DUPLICATE_MAX_DISTANCE
from google_drive import find_file_by_sha, upload_original_bytes
from google_sheets import append_records, read_records, update_queue_jobs
from models import JobState, QueueJob, UploadResult, utc_now_iso
from upload_manager import PreparedUpload
from utils import hamming_distance_hex


_UPLOAD_LOCK = threading.RLock()


def _is_active_or_confirmed(record: dict[str, str]) -> bool:
    return record.get("estado", "") not in {JobState.ERROR.value}


def find_exact_duplicates(
    records: list[dict[str, str]],
    sha256: str,
) -> list[dict[str, str]]:
    """Busca duplicados binarios que no sean filas fallidas."""
    return [
        record
        for record in records
        if record.get("sha256") == sha256 and _is_active_or_confirmed(record)
    ]


def find_visual_duplicates(
    records: list[dict[str, str]],
    perceptual_hash: str,
) -> list[dict[str, str]]:
    """Busca imágenes visualmente similares por distancia de Hamming."""
    if not perceptual_hash:
        return []
    matches = []
    for record in records:
        stored_hash = record.get("perceptual_hash", "")
        if hamming_distance_hex(perceptual_hash, stored_hash) <= VISUAL_DUPLICATE_MAX_DISTANCE:
            matches.append(record)
    return matches


def _build_jobs(upload: PreparedUpload, user_email: str) -> list[QueueJob]:
    return [
        QueueJob(
            sha256=upload.sha256,
            perceptual_hash=upload.perceptual_hash,
            nombre_original=upload.filename,
            mime_type=upload.mime_type,
            tipo_documento=upload.document_type,
            pagina_pdf=page_label,
            usuario=user_email,
            estado=JobState.SUBIENDO,
        )
        for page_label in upload.page_labels
    ]


def enqueue_upload(
    credentials: Credentials,
    spreadsheet_id: str,
    input_folder_id: str,
    upload: PreparedUpload,
    user_email: str,
    allow_visual_duplicate: bool,
    progress_callback: Callable[[float], None] | None = None,
) -> UploadResult:
    """Persiste el original y crea uno o más jobs con checkpoints."""
    with _UPLOAD_LOCK:
        queue_records = read_records(credentials, spreadsheet_id, "OCR_COLA")
        exact = find_exact_duplicates(queue_records, upload.sha256)
        if exact:
            state = exact[0].get("estado", "PENDIENTE")
            return UploadResult(
                status="DUPLICADO_EXACTO",
                filename=upload.filename,
                message=f"Ya existe en la cola con estado {state}.",
            )

        visual = find_visual_duplicates(queue_records, upload.perceptual_hash)
        if visual and not allow_visual_duplicate:
            return UploadResult(
                status="DUPLICADO_VISUAL",
                filename=upload.filename,
                message="Existe una imagen visualmente similar; requiere confirmación explícita.",
            )

        jobs = _build_jobs(upload, user_email)
        append_records(
            credentials,
            spreadsheet_id,
            "OCR_COLA",
            [job.as_sheet_record() for job in jobs],
        )

        try:
            drive_file_id = find_file_by_sha(
                credentials,
                input_folder_id,
                upload.sha256,
            )
            if not drive_file_id:
                drive_file_id = upload_original_bytes(
                    credentials=credentials,
                    folder_id=input_folder_id,
                    filename=upload.filename,
                    mime_type=upload.mime_type,
                    data=upload.data,
                    sha256=upload.sha256,
                    progress_callback=progress_callback,
                )

            now = utc_now_iso()
            updated_count = update_queue_jobs(
                credentials,
                spreadsheet_id,
                {
                    job.job_id: {
                        "drive_file_id": drive_file_id,
                        "estado": JobState.PENDIENTE.value,
                        "updated_at": now,
                    }
                    for job in jobs
                },
            )
            if updated_count != len(jobs):
                raise RuntimeError("No se actualizaron todos los checkpoints.")
            try:
                append_records(
                    credentials,
                    spreadsheet_id,
                    "LOGS",
                    [
                        {
                            "log_id": str(uuid4()),
                            "accion": "CARGA_PERSISTENTE",
                            "job_id": jobs[0].job_id,
                            "sha256": upload.sha256,
                            "detalle": json.dumps(
                                {
                                    "archivo": upload.filename,
                                    "jobs": len(jobs),
                                    "duplicado_visual_aceptado": bool(visual),
                                },
                                ensure_ascii=False,
                            ),
                            "usuario": user_email,
                            "created_at": now,
                        }
                    ],
                )
            except Exception:
                pass
            return UploadResult(
                status="PERSISTIDO",
                filename=upload.filename,
                message="Original guardado en Drive y cola actualizada.",
                jobs_created=len(jobs),
            )
        except Exception as exc:
            now = utc_now_iso()
            try:
                update_queue_jobs(
                    credentials,
                    spreadsheet_id,
                    {
                        job.job_id: {
                            "estado": JobState.ERROR.value,
                            "error": f"Fallo de persistencia: {type(exc).__name__}",
                            "updated_at": now,
                        }
                        for job in jobs
                    },
                )
            except Exception:
                pass
            raise


def recover_upload_checkpoints(
    credentials: Credentials,
    spreadsheet_id: str,
    input_folder_id: str,
) -> int:
    """Recupera SUBIENDO cuando Drive recibió el archivo antes del reinicio."""
    with _UPLOAD_LOCK:
        records = read_records(credentials, spreadsheet_id, "OCR_COLA")
        uploading = [
            record
            for record in records
            if record.get("estado") == JobState.SUBIENDO.value
            and not record.get("drive_file_id")
        ]
        grouped: dict[str, list[dict[str, str]]] = {}
        for record in uploading:
            grouped.setdefault(record.get("sha256", ""), []).append(record)

        recovered = 0
        for sha256, group in grouped.items():
            if not sha256:
                continue
            drive_file_id = find_file_by_sha(credentials, input_folder_id, sha256)
            if not drive_file_id:
                continue
            now = utc_now_iso()
            count = update_queue_jobs(
                credentials,
                spreadsheet_id,
                {
                    record["job_id"]: {
                        "drive_file_id": drive_file_id,
                        "estado": JobState.PENDIENTE.value,
                        "error": "",
                        "updated_at": now,
                    }
                    for record in group
                },
            )
            recovered += count
        return recovered
