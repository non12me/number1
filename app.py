"""Punto de entrada de OCR Documental Web.

Fase 2: conexión segura con Google Drive y Google Sheets mediante OAuth 2.0.
"""

from __future__ import annotations

import platform
from typing import Any

import streamlit as st

from config import APP_NAME, APP_VERSION, PROJECT_PHASE
from google_auth import GoogleConfigurationError, get_google_credentials
from google_drive import ensure_database_spreadsheet, test_drive_connection
from google_sheets import test_sheets_connection


st.set_page_config(
    page_title=APP_NAME,
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed",
)


SECRET_NAMES = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "GOOGLE_SHEET_ID",
    "DRIVE_INPUT_FOLDER_ID",
    "DRIVE_CONFIRMED_FOLDER_ID",
    "GEMINI_API_KEY",
    "APP_ALLOWED_EMAILS",
    "APP_ADMIN_EMAIL",
)

GOOGLE_OAUTH_SECRET_NAMES = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
)


def get_secret(name: str) -> str:
    """Obtiene un secreto sin mostrar ni registrar su contenido."""
    try:
        value: Any = st.secrets.get(name, "")
    except (FileNotFoundError, KeyError):
        return ""
    return str(value).strip() if value is not None else ""


def get_oauth_credentials():
    """Construye credenciales OAuth a partir de Streamlit Secrets."""
    return get_google_credentials(
        client_id=get_secret("GOOGLE_CLIENT_ID"),
        client_secret=get_secret("GOOGLE_CLIENT_SECRET"),
        refresh_token=get_secret("GOOGLE_REFRESH_TOKEN"),
    )


def render_header() -> None:
    """Muestra el encabezado principal."""
    st.title("📄 OCR documental")
    st.caption("Peajes, boletas y facturas · Aplicación web")

    column_a, column_b, column_c = st.columns(3)
    column_a.metric("Versión", APP_VERSION)
    column_b.metric("Fase", PROJECT_PHASE)
    column_c.metric("Gemini", "Desactivado")


def render_environment() -> None:
    """Muestra información pública del entorno."""
    with st.expander("Comprobación del entorno"):
        st.dataframe(
            [
                {"Componente": "Python", "Estado": platform.python_version()},
                {"Componente": "Streamlit", "Estado": st.__version__},
                {"Componente": "OAuth", "Estado": "Propietario con acceso offline"},
                {"Componente": "Alcance Google", "Estado": "drive.file"},
                {"Componente": "Gemini", "Estado": "Desactivado"},
            ],
            hide_index=True,
            width="stretch",
        )


def render_secret_status() -> None:
    """Indica qué variables existen sin revelar sus valores."""
    st.subheader("Estado de configuración")
    rows = []
    for name in SECRET_NAMES:
        configured = bool(get_secret(name))
        rows.append(
            {
                "Variable": name,
                "Estado": "✅ Configurada" if configured else "⏳ Pendiente",
            }
        )

    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("La aplicación comprueba solamente si existe un valor; nunca lo muestra.")


def render_bootstrap_sheet() -> None:
    """Crea o recupera de forma idempotente la hoja base del proyecto."""
    st.subheader("Preparar Google Drive y Sheets")

    missing_oauth = [name for name in GOOGLE_OAUTH_SECRET_NAMES if not get_secret(name)]
    if missing_oauth:
        st.warning(
            "Primero configura GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y "
            "GOOGLE_REFRESH_TOKEN en Streamlit Secrets."
        )
        return

    sheet_id = get_secret("GOOGLE_SHEET_ID")

    if not sheet_id:
        st.info(
            "OAuth ya está configurado. El siguiente botón comprobará Drive y "
            "creará una sola hoja llamada OCR_DOCUMENTAL_DB. Si ya fue creada "
            "por la aplicación, la reutilizará."
        )
        confirmed = st.checkbox(
            "Confirmo que deseo preparar la hoja persistente OCR_DOCUMENTAL_DB",
            key="confirm_bootstrap_sheet",
        )

        if st.button(
            "Verificar OAuth y preparar Google Sheet",
            type="primary",
            disabled=not confirmed,
            width="stretch",
        ):
            try:
                with st.spinner("Comprobando autorización de Google..."):
                    credentials = get_oauth_credentials()
                    drive_result = test_drive_connection(credentials)
                    created = ensure_database_spreadsheet(credentials)

                account_email = drive_result.get("email", "Cuenta autorizada")
                action = "creada" if created else "encontrada"
                st.success(
                    f"✅ Conexión correcta. Hoja OCR_DOCUMENTAL_DB {action}. "
                    f"Cuenta: {account_email}"
                )
                st.info(
                    "Ahora abre Google Drive, busca OCR_DOCUMENTAL_DB, ábrela y "
                    "copia únicamente su ID desde la barra de direcciones. Luego "
                    "guárdalo como GOOGLE_SHEET_ID en Streamlit Secrets."
                )
            except GoogleConfigurationError as exc:
                st.error(f"Configuración incompleta: {exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(
                    "Google rechazó la operación. Revisa los permisos, APIs y "
                    f"refresh token. Tipo de error: {type(exc).__name__}."
                )
        return

    st.success("✅ GOOGLE_SHEET_ID está configurado.")
    if st.button(
        "Probar conexión completa con Drive y Sheets",
        type="primary",
        width="stretch",
    ):
        try:
            with st.spinner("Probando Google Drive y Google Sheets..."):
                credentials = get_oauth_credentials()
                drive_result = test_drive_connection(credentials)
                sheet_result = test_sheets_connection(credentials, sheet_id)

            st.success("✅ Google Drive y Google Sheets funcionan correctamente.")
            st.dataframe(
                [
                    {"Prueba": "Cuenta OAuth", "Resultado": drive_result.get("email", "OK")},
                    {"Prueba": "Drive API", "Resultado": "OK"},
                    {"Prueba": "Sheets API", "Resultado": "OK"},
                    {"Prueba": "Hoja", "Resultado": sheet_result.get("title", "OK")},
                    {"Prueba": "Zona horaria", "Resultado": sheet_result.get("timeZone", "No definida")},
                ],
                hide_index=True,
                width="stretch",
            )
        except GoogleConfigurationError as exc:
            st.error(f"Configuración incompleta: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(
                "No se completó la prueba. Revisa Streamlit Secrets y las APIs "
                f"habilitadas. Tipo de error: {type(exc).__name__}."
            )


def main() -> None:
    """Ejecuta la interfaz de la Fase 2."""
    render_header()
    st.info(
        "Fase 2: autorización OAuth del propietario y prueba de persistencia. "
        "No se procesan documentos y Gemini no consume tokens."
    )
    render_environment()
    render_secret_status()
    render_bootstrap_sheet()
    st.caption(f"{APP_NAME} · {APP_VERSION}")


if __name__ == "__main__":
    main()
