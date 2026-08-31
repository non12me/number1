"""Pruebas idempotentes de confirmación sin Google real."""

from __future__ import annotations

from copy import deepcopy

import confirmation
from confirmation import confirm_review, suggested_filename
from utils import encode_json_for_sheet


def valid_payload() -> dict:
    values = {
        "concesionaria": "Concesión Sur",
        "lugar": "Lurín",
        "estacion": "Km 25",
        "fecha": "2026-08-28",
        "hora": "07:05",
        "placa": "ABC-123",
        "serie_numero": "T001-0001",
        "subtotal": "10.00",
        "igv": "1.80",
        "monto_total": "11.80",
        "moneda": "PEN",
    }
    return {
        "pages": [],
        "extraction": {
            "document_type": "PEAJE",
            "fields": {
                name: {
                    "value": value,
                    "confidence_ocr": 0.90,
                    "confidence_rule": 0.90,
                    "confidence_final": 0.90,
                    "source": "OCR",
                    "warnings": [],
                }
                for name, value in values.items()
            },
            "global_confidence": 0.90,
            "valid": True,
            "blocking_validations": [],
            "warnings": [],
        },
    }


def test_filename_is_stable_and_has_no_path_components() -> None:
    record = {
        "job_id": "job-12345678",
        "sha256": "abcdef1234567890",
        "tipo_documento": "PEAJE",
        "nombre_original": "../../foto.jpg",
        "mime_type": "image/jpeg",
    }
    name = suggested_filename(
        record,
        {"fecha": "2026-08-28", "placa": "ABC-123", "serie_numero": "T/001"},
    )
    assert "/" not in name
    assert ".." not in name
    assert name.endswith(".jpg")


def test_second_confirmation_does_not_duplicate_or_move_again(monkeypatch) -> None:
    payload = valid_payload()
    values = {
        name: str(data["value"])
        for name, data in payload["extraction"]["fields"].items()
    }
    queue = [{
        "job_id": "job-1",
        "sha256": "a" * 64,
        "drive_file_id": "drive-1",
        "nombre_original": "peaje.jpg",
        "mime_type": "image/jpeg",
        "tipo_documento": "PEAJE",
        "estado": "NECESITA_REVISION",
        "datos_extraidos_json": encode_json_for_sheet(payload),
        "lock_owner": "",
        "lock_until": "",
    }]
    sheets = {"OCR_COLA": queue, "CORRECCIONES": [], "PEAJES": [], "LOGS": []}
    calls = {"move": 0, "upsert": 0}

    def fake_read(_credentials, _spreadsheet_id, sheet_name):
        return deepcopy(sheets.get(sheet_name, []))

    def fake_update(_credentials, _spreadsheet_id, updates):
        count = 0
        for row in sheets["OCR_COLA"]:
            if row["job_id"] in updates:
                row.update(updates[row["job_id"]])
                count += 1
        return count

    def fake_append(_credentials, _spreadsheet_id, sheet_name, records):
        rows = list(records)
        sheets.setdefault(sheet_name, []).extend(deepcopy(rows))
        return len(rows)

    def fake_upsert(_credentials, _spreadsheet_id, sheet_name, _key, key_value, record):
        calls["upsert"] += 1
        existing = next(
            (row for row in sheets[sheet_name] if row.get("job_id") == key_value), None
        )
        if existing:
            existing.update(deepcopy(record))
            return "UPDATED"
        sheets[sheet_name].append(deepcopy(record))
        return "INSERTED"

    def fake_move(*_args, **_kwargs):
        calls["move"] += 1
        return {"id": "drive-1", "name": "final.jpg", "parents": "confirmed"}

    monkeypatch.setattr(confirmation, "_read_records", fake_read)
    monkeypatch.setattr(confirmation, "_update_queue_jobs", fake_update)
    monkeypatch.setattr(confirmation, "_append_records", fake_append)
    monkeypatch.setattr(confirmation, "_upsert_record", fake_upsert)
    monkeypatch.setattr(confirmation, "_move_file_to_folder", fake_move)

    first = confirm_review(None, "sheet", "confirmed", "job-1", values, "admin@test.pe")
    second = confirm_review(None, "sheet", "confirmed", "job-1", values, "admin@test.pe")

    assert first["status"] == "CONFIRMADO"
    assert second["status"] == "YA_CONFIRMADO"
    assert len(sheets["PEAJES"]) == 1
    assert calls == {"move": 1, "upsert": 1}
    assert sheets["OCR_COLA"][0]["estado"] == "CONFIRMADO"


def test_shared_pdf_moves_only_after_last_logical_job(monkeypatch) -> None:
    payload = valid_payload()
    values = {
        name: str(data["value"])
        for name, data in payload["extraction"]["fields"].items()
    }
    encoded = encode_json_for_sheet(payload)
    queue = [
        {
            "job_id": job_id,
            "sha256": "b" * 64,
            "drive_file_id": "drive-pdf",
            "nombre_original": "lote.pdf",
            "mime_type": "application/pdf",
            "tipo_documento": "PEAJE",
            "estado": "NECESITA_REVISION",
            "datos_extraidos_json": encoded,
            "lock_owner": "",
            "lock_until": "",
        }
        for job_id in ("job-1", "job-2")
    ]
    sheets = {"OCR_COLA": queue, "CORRECCIONES": [], "PEAJES": [], "LOGS": []}
    moved = []

    monkeypatch.setattr(
        confirmation,
        "_read_records",
        lambda _c, _s, name: deepcopy(sheets.get(name, [])),
    )

    def update(_c, _s, updates):
        for row in queue:
            if row["job_id"] in updates:
                row.update(updates[row["job_id"]])
        return len(updates)

    def append(_c, _s, name, records):
        rows = list(records)
        sheets.setdefault(name, []).extend(deepcopy(rows))
        return len(rows)

    def upsert(_c, _s, name, _key, key_value, record):
        sheets[name] = [row for row in sheets[name] if row.get("job_id") != key_value]
        sheets[name].append(deepcopy(record))
        return "INSERTED"

    monkeypatch.setattr(confirmation, "_update_queue_jobs", update)
    monkeypatch.setattr(confirmation, "_append_records", append)
    monkeypatch.setattr(confirmation, "_upsert_record", upsert)
    monkeypatch.setattr(
        confirmation,
        "_move_file_to_folder",
        lambda *_args: moved.append(True) or {},
    )

    first = confirm_review(None, "sheet", "confirmed", "job-1", values, "admin@test.pe")
    second = confirm_review(None, "sheet", "confirmed", "job-2", values, "admin@test.pe")
    assert first["moved"] is False
    assert second["moved"] is True
    assert len(moved) == 1
