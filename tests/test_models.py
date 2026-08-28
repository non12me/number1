"""Pruebas del esquema persistente de la cola."""

from __future__ import annotations

from models import DocumentType, JobState, QueueJob


def test_all_required_queue_states_exist() -> None:
    assert {state.value for state in JobState} == {
        "SUBIENDO",
        "PENDIENTE",
        "PROCESANDO",
        "EXTRAIDO_LOCAL",
        "NECESITA_REVISION",
        "CONFIRMANDO",
        "CONFIRMADO",
        "PENDIENTE_RESULTADO",
        "ERROR",
    }


def test_queue_job_serializes_for_sheets() -> None:
    job = QueueJob(
        sha256="a" * 64,
        nombre_original="prueba.png",
        mime_type="image/png",
        tipo_documento=DocumentType.PEAJE,
        usuario="usuario@example.com",
    )
    record = job.as_sheet_record()
    assert record["estado"] == "SUBIENDO"
    assert record["tipo_documento"] == "PEAJE"
    assert record["gemini_usado"] == "FALSE"
    assert record["job_id"]
