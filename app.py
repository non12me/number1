"""Punto de entrada de OCR Web

Fase 1: prueba mínima de despliegue en Streamlit Community Cloud.
"""

from __future__ import annotations

import platform

import streamlit as st

from config import APP_NAME, APP_VERSION, PROJECT_PHASE


st.set_page_config(
    page_title=APP_NAME,
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def render_header() -> None:
    """Muestra el encabezado principal de la aplicación."""
    st.title("📄 OCR documental")
    st.caption("Peajes, boletas y facturas · Aplicación web")


def render_status() -> None:
    """Muestra el estado verificable de la Fase 1."""
    st.success("✅ Fase 1 desplegada correctamente")

    column_a, column_b, column_c = st.columns(3)
    column_a.metric("Versión", APP_VERSION)
    column_b.metric("Fase", PROJECT_PHASE)
    column_c.metric("Modo", "MOCK")

    st.info(
        "Esta versión no solicita credenciales, no carga documentos y no usa "
        "tokens de Gemini. Su única finalidad es comprobar el despliegue web."
    )


def render_checks() -> None:
    """Muestra comprobaciones técnicas sin revelar información sensible."""
    st.subheader("Comprobación del entorno")
    checks = {
        "Aplicación web": "OK",
        "Python": platform.python_version(),
        "Streamlit": st.__version__,
        "Persistencia de archivos": "Se configurará en la Fase 3",
        "Google Drive y Sheets": "Se configurarán en la Fase 2",
        "Gemini": "Desactivado",
    }
    st.dataframe(
        [{"Componente": key, "Estado": value} for key, value in checks.items()],
        hide_index=True,
        width="stretch",
    )


def main() -> None:
    """Ejecuta la pantalla inicial."""
    render_header()
    render_status()
    render_checks()
    st.caption(f"{APP_NAME} · {APP_VERSION}")


if __name__ == "__main__":
    main()
