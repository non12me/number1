"""Pruebas de movimiento Drive y upsert Sheets sin llamadas externas."""

from __future__ import annotations

import google_drive
import google_sheets


class Response:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


def test_move_reuses_file_and_preserves_private_properties(monkeypatch) -> None:
    captured = {}

    class Files:
        def get(self, **_kwargs):
            return Response({
                "id": "file-1",
                "name": "old.jpg",
                "parents": ["input"],
                "trashed": False,
                "appProperties": {"sha256": "abc", "ocr_status": "input"},
            })

        def update(self, **kwargs):
            captured.update(kwargs)
            return Response({"id": "file-1", "name": "final.jpg", "parents": ["confirmed"]})

    class Service:
        def files(self):
            return Files()

    monkeypatch.setattr(google_drive, "build_drive_service", lambda _credentials: Service())
    result = google_drive.move_file_to_folder(None, "file-1", "confirmed", "final.jpg")
    assert captured["fileId"] == "file-1"
    assert captured["addParents"] == "confirmed"
    assert captured["removeParents"] == "input"
    assert captured["body"]["appProperties"]["sha256"] == "abc"
    assert captured["body"]["appProperties"]["ocr_status"] == "confirmed"
    assert result["name"] == "final.jpg"


def test_upsert_updates_existing_job_instead_of_appending(monkeypatch) -> None:
    calls = {"append": 0, "update": 0, "range": ""}
    headers = google_sheets.SHEET_HEADERS["PEAJES"]
    existing = [headers, ["7", "job-1"]]

    class Values:
        def get(self, **_kwargs):
            return Response({"values": existing})

        def append(self, **_kwargs):
            calls["append"] += 1
            return Response({})

        def update(self, **kwargs):
            calls["update"] += 1
            calls["range"] = kwargs["range"]
            return Response({})

    values = Values()

    class Spreadsheets:
        def values(self):
            return values

    class Service:
        def spreadsheets(self):
            return Spreadsheets()

    monkeypatch.setattr(google_sheets, "build_sheets_service", lambda _credentials: Service())
    status = google_sheets.upsert_record(
        None,
        "sheet",
        "PEAJES",
        "job_id",
        "job-1",
        {"numero": 7, "job_id": "job-1", "monto_total": "11.80"},
    )
    assert status == "UPDATED"
    assert calls["append"] == 0
    assert calls["update"] == 1
    assert calls["range"].startswith("'PEAJES'!A2:")
