"""Contrato visible de la aplicación final."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from config import APP_VERSION, PROJECT_PHASE


def test_final_version_and_navigation_are_present() -> None:
    assert APP_VERSION == "1.0.0"
    assert PROJECT_PHASE == "Versión final"
    source = Path("app.py").read_text(encoding="utf-8")
    for label in (
        "Dashboard", "Cargar", "Procesar", "Revisión", "Resultados",
        "Conocimiento", "Configuración",
    ):
        assert f'"{label}"' in source


def test_app_starts_without_secrets_or_exceptions() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=20).run()
    assert not app.exception
    labels = {tab.label for tab in app.tabs}
    assert {
        "Dashboard", "Cargar", "Procesar", "Revisión", "Resultados",
        "Conocimiento", "Configuración",
    } <= labels
