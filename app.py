"""Aplicación web OCR: infraestructura persistente y carga segura."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import streamlit as st

from config import (
    APP_NAME,
    APP_VERSION,
    DRIVE_CONFIRMED_FOLDER_NAME,
    DRIVE_INPUT_FOLDER_NAME,
    MAX_FILES_PER_UPLOAD,
    MAX_FILE_SIZE_MB,
    PROJECT_PHASE,
)
from google_auth import get_google_credentials
from google_drive import ensure_storage_folders, validate_folder
from google_sheets import ensure_workbook_structure, read_records
from local_ocr import OCRModelLoadError, get_ocr_engine
from models import DocumentType
from ocr_worker import (
    process_one_pending_job,
    recover_expired_ocr_jobs,
    retry_local_ocr_errors,
)
from queue_manager import enqueue_upload, recover_upload_checkpoints
from upload_manager import prepare_upload
from utils import decode_json_from_sheet, pdf_page_count


st.set_page_config(
    page_title=APP_NAME,
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def get_secret(name: str) -> str:
    """Lee un secreto sin mostrar su valor."""
    try:
        value: Any = st.secrets.get(name, "")
    except (FileNotFoundError, KeyError):
        return ""
    return str(value).strip() if value is not None else ""


def get_credentials():
    """Obtiene credenciales OAuth del propietario."""
    return get_google_credentials(
        client_id=get_secret("GOOGLE_CLIENT_ID"),
        client_secret=get_secret("GOOGLE_CLIENT_SECRET"),
        refresh_token=get_secret("GOOGLE_REFRESH_TOKEN"),
    )


def infrastructure_ready() -> bool:
    """Indica si los IDs persistentes ya están configurados."""
    required = (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REFRESH_TOKEN",
        "GOOGLE_SHEET_ID",
        "DRIVE_INPUT_FOLDER_ID",
        "DRIVE_CONFIRMED_FOLDER_ID",
    )
    return all(get_secret(name) for name in required)


def render_header() -> None:
    st.title("📄 OCR documental")
    st.caption("Peajes, boletas y facturas · Persistencia en Drive y Sheets")
    first, second, third, fourth = st.columns(4)
    first.metric("Versión", APP_VERSION)
    second.metric("Fase", PROJECT_PHASE)
    third.metric("Carga máxima", f"{MAX_FILES_PER_UPLOAD} archivos")
    fourth.metric("OCR", "CPU local")


def render_setup() -> None:
    st.subheader("Preparar almacenamiento persistente")
    sheet_id = get_secret("GOOGLE_SHEET_ID")
    oauth_ready = all(
        get_secret(name)
        for name in (
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "GOOGLE_REFRESH_TOKEN",
        )
    )

    if not oauth_ready or not sheet_id:
        st.error("Falta completar la Fase 2 en Streamlit Secrets.")
        return

    st.info(
        "Este botón crea o recupera OCR_ENTRADA y OCR_CONFIRMADOS, y prepara "
        "las nueve pestañas de OCR_DOCUMENTAL_DB. Puede pulsarse nuevamente sin duplicarlas."
    )
    if st.button("Crear o verificar estructura de la Fase 3", type="primary", width="stretch"):
        try:
            with st.spinner("Preparando Drive y Google Sheets..."):
                credentials = get_credentials()
                folder_result = ensure_storage_folders(credentials)
                workbook_result = ensure_workbook_structure(credentials, sheet_id)
            input_action = "creada" if folder_result["input_created"] else "encontrada"
            confirmed_action = (
                "creada" if folder_result["confirmed_created"] else "encontrada"
            )
            st.success(
                f"✅ {DRIVE_INPUT_FOLDER_NAME} {input_action}; "
                f"{DRIVE_CONFIRMED_FOLDER_NAME} {confirmed_action}; "
                f"{workbook_result['sheet_count']} pestañas verificadas."
            )
            st.warning(
                "Si los IDs de las carpetas siguen pendientes, abre cada carpeta desde "
                "Google Drive y guarda su ID en Streamlit Secrets."
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"No se pudo preparar la estructura. Error: {type(exc).__name__}.")

    input_id = get_secret("DRIVE_INPUT_FOLDER_ID")
    confirmed_id = get_secret("DRIVE_CONFIRMED_FOLDER_ID")
    if not input_id or not confirmed_id:
        st.warning("Los dos IDs de carpetas todavía están pendientes en Streamlit Secrets.")
        return

    if st.button("Validar IDs de las dos carpetas", width="stretch"):
        try:
            credentials = get_credentials()
            validate_folder(credentials, input_id, DRIVE_INPUT_FOLDER_NAME)
            validate_folder(credentials, confirmed_id, DRIVE_CONFIRMED_FOLDER_NAME)
            ensure_workbook_structure(credentials, sheet_id)
            st.success("✅ Carpetas y pestañas configuradas correctamente.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Configuración inválida. Error: {type(exc).__name__}.")


def _collect_files():
    uploaded = st.file_uploader(
        "Arrastra o selecciona JPG, JPEG, PNG, WEBP o PDF",
        type=["jpg", "jpeg", "png", "webp", "pdf"],
        accept_multiple_files=True,
        max_upload_size=MAX_FILE_SIZE_MB,
        width="stretch",
    )
    camera = st.camera_input(
        "Tomar una fotografía desde celular o cámara",
        resolution="1080p",
        width="stretch",
    )
    files = list(uploaded or [])
    if camera is not None:
        files.append(camera)
    return files


def render_upload() -> None:
    st.subheader("Cargar originales")
    if not infrastructure_ready():
        st.warning("Completa y valida la infraestructura antes de cargar archivos.")
        return

    global_type = st.selectbox(
        "Tipo documental predeterminado",
        options=[item.value for item in DocumentType],
    )
    files = _collect_files()
    if not files:
        st.caption("Los archivos seleccionados permanecen en memoria solo hasta enviarlos a Drive.")
        return
    if len(files) > MAX_FILES_PER_UPLOAD:
        st.error(f"Seleccionaste {len(files)} archivos. El máximo por carga es {MAX_FILES_PER_UPLOAD}.")
        return

    settings: list[dict[str, Any]] = []
    for index, uploaded_file in enumerate(files):
        data = uploaded_file.getvalue()
        key = f"{index}_{uploaded_file.name}_{len(data)}"
        with st.expander(f"{index + 1}. {uploaded_file.name}", expanded=True):
            document_type = st.selectbox(
                "Tipo",
                options=[item.value for item in DocumentType],
                index=[item.value for item in DocumentType].index(global_type),
                key=f"type_{key}",
            )
            separate_pages = False
            if data.startswith(b"%PDF-"):
                try:
                    pages = pdf_page_count(data)
                    st.caption(f"PDF detectado: {pages} página(s).")
                    mode = st.radio(
                        "Interpretación del PDF",
                        options=["Todas las páginas forman un documento", "Cada página es un documento"],
                        key=f"pdf_{key}",
                    )
                    separate_pages = mode == "Cada página es un documento"
                except ValueError as exc:
                    st.error(str(exc))
            settings.append(
                {
                    "file": uploaded_file,
                    "data": data,
                    "document_type": document_type,
                    "separate_pages": separate_pages,
                }
            )

    allow_similar = st.checkbox(
        "Conservar imágenes visualmente similares si se detectan",
        help="Los duplicados SHA-256 exactos nunca se vuelven a subir.",
    )
    confirm = st.checkbox(
        "Confirmo que deseo guardar estos originales en OCR_ENTRADA",
        key="confirm_persistent_upload",
    )
    if not st.button(
        "Guardar en Drive y crear cola",
        type="primary",
        disabled=not confirm,
        width="stretch",
    ):
        return

    credentials = get_credentials()
    sheet_id = get_secret("GOOGLE_SHEET_ID")
    input_folder_id = get_secret("DRIVE_INPUT_FOLDER_ID")
    user_email = get_secret("APP_ADMIN_EMAIL") or "ADMIN_NO_CONFIGURADO"
    ensure_workbook_structure(credentials, sheet_id)

    overall = st.progress(0.0, text="Iniciando carga...")
    results = []
    total = len(settings)
    for index, item in enumerate(settings):
        filename = item["file"].name
        try:
            prepared = prepare_upload(
                filename=filename,
                data=item["data"],
                document_type=item["document_type"],
                separate_pdf_pages=item["separate_pages"],
            )

            def update_progress(file_progress: float, current=index) -> None:
                value = min(1.0, (current + file_progress) / total)
                overall.progress(value, text=f"Subiendo {filename}...")

            result = enqueue_upload(
                credentials=credentials,
                spreadsheet_id=sheet_id,
                input_folder_id=input_folder_id,
                upload=prepared,
                user_email=user_email,
                allow_visual_duplicate=allow_similar,
                progress_callback=update_progress,
            )
            results.append(result.model_dump())
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "status": "ERROR",
                    "filename": filename,
                    "message": f"{type(exc).__name__}: no se completó la persistencia.",
                    "jobs_created": 0,
                }
            )
        overall.progress((index + 1) / total, text=f"Procesado {index + 1} de {total}")

    st.session_state["last_upload_results"] = results
    st.dataframe(results, hide_index=True, width="stretch")
    persisted = sum(result["status"] == "PERSISTIDO" for result in results)
    if persisted:
        st.success(f"✅ {persisted} archivo(s) persistidos. Ya puedes cerrar la página sin perderlos.")


def render_queue() -> None:
    st.subheader("Cola persistente")
    if not infrastructure_ready():
        st.warning("La infraestructura todavía no está completa.")
        return
    try:
        credentials = get_credentials()
        sheet_id = get_secret("GOOGLE_SHEET_ID")
        records = read_records(credentials, sheet_id, "OCR_COLA")
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudo leer OCR_COLA. Error: {type(exc).__name__}.")
        return

    counts = Counter(record.get("estado", "SIN_ESTADO") for record in records)
    columns = st.columns(6)
    columns[0].metric("Jobs totales", len(records))
    columns[1].metric("Pendientes", counts.get("PENDIENTE", 0))
    columns[2].metric("Procesando", counts.get("PROCESANDO", 0))
    columns[3].metric("OCR local", counts.get("EXTRAIDO_LOCAL", 0))
    columns[4].metric("Revisión", counts.get("NECESITA_REVISION", 0))
    columns[5].metric("Errores", counts.get("ERROR", 0))

    if st.button("Recuperar cargas interrumpidas", width="stretch"):
        recovered = recover_upload_checkpoints(
            credentials,
            sheet_id,
            get_secret("DRIVE_INPUT_FOLDER_ID"),
        )
        if recovered:
            st.success(f"✅ Se recuperaron {recovered} job(s).")
            st.rerun()
        else:
            st.info("No había cargas recuperables.")

    visible_columns = [
        "job_id",
        "nombre_original",
        "tipo_documento",
        "pagina_pdf",
        "estado",
        "usuario",
        "created_at",
        "updated_at",
    ]
    visible = [
        {column: record.get(column, "") for column in visible_columns}
        for record in reversed(records[-100:])
    ]
    if visible:
        st.dataframe(visible, hide_index=True, width="stretch")
    else:
        st.info("OCR_COLA todavía no contiene documentos.")


def _initialize_worker_state() -> None:
    defaults = {
        "ocr_worker_owner": str(uuid4()),
        "ocr_batch_running": False,
        "ocr_batch_stop": False,
        "ocr_batch_target": 1,
        "ocr_batch_remaining": 0,
        "ocr_batch_processed": 0,
        "ocr_batch_started_at": "",
        "ocr_batch_results": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


@st.fragment(run_every="1s")
def render_ocr_worker() -> None:
    _initialize_worker_state()
    running = bool(st.session_state["ocr_batch_running"])
    batch_size = st.selectbox(
        "Cantidad del lote",
        options=[1, 5, 10],
        disabled=running,
        key="ocr_batch_size_selector",
    )
    start_column, stop_column = st.columns(2)
    if start_column.button(
        "Procesar siguientes documentos",
        type="primary",
        disabled=running,
        width="stretch",
    ):
        st.session_state["ocr_batch_running"] = True
        st.session_state["ocr_batch_stop"] = False
        st.session_state["ocr_batch_target"] = int(batch_size)
        st.session_state["ocr_batch_remaining"] = int(batch_size)
        st.session_state["ocr_batch_processed"] = 0
        st.session_state["ocr_batch_started_at"] = datetime.now(timezone.utc).isoformat()
        st.session_state["ocr_batch_results"] = []
        running = True

    if stop_column.button(
        "Detener después del actual",
        disabled=not running,
        width="stretch",
    ):
        st.session_state["ocr_batch_stop"] = True

    target = int(st.session_state["ocr_batch_target"])
    processed = int(st.session_state["ocr_batch_processed"])
    progress_value = min(1.0, processed / max(1, target))
    st.progress(progress_value, text=f"Completados {processed} de {target}")

    if running and st.session_state["ocr_batch_stop"]:
        st.session_state["ocr_batch_running"] = False
        st.session_state["ocr_batch_stop"] = False
        st.info("Lote detenido después del último documento terminado.")
        running = False

    if running and int(st.session_state["ocr_batch_remaining"]) > 0:
        credentials = get_credentials()
        result = process_one_pending_job(
            credentials=credentials,
            spreadsheet_id=get_secret("GOOGLE_SHEET_ID"),
            owner=str(st.session_state["ocr_worker_owner"]),
            user_email=get_secret("APP_ADMIN_EMAIL") or "ADMIN_NO_CONFIGURADO",
        )
        results = list(st.session_state["ocr_batch_results"])
        results.append(result)
        st.session_state["ocr_batch_results"] = results[-10:]

        status = result.get("status", "")
        if status not in {"OCUPADO", "SIN_PENDIENTES", "LOCK_PERDIDO"}:
            st.session_state["ocr_batch_processed"] += 1
            st.session_state["ocr_batch_remaining"] -= 1
        if status in {"SIN_PENDIENTES", "LOCK_PERDIDO"}:
            st.session_state["ocr_batch_running"] = False
        if int(st.session_state["ocr_batch_remaining"]) <= 0:
            st.session_state["ocr_batch_running"] = False

    results = list(st.session_state["ocr_batch_results"])
    if results:
        st.dataframe(results, hide_index=True, width="stretch")
    if st.session_state["ocr_batch_running"]:
        st.info("Procesando secuencialmente. Mantén esta pestaña abierta.")
    elif results:
        st.success("El lote actual terminó o quedó detenido con checkpoint guardado.")


def render_ocr_result_viewer() -> None:
    try:
        records = read_records(
            get_credentials(),
            get_secret("GOOGLE_SHEET_ID"),
            "OCR_COLA",
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudieron leer los resultados. Error: {type(exc).__name__}.")
        return
    processed = [
        record
        for record in records
        if record.get("estado") in {"EXTRAIDO_LOCAL", "NECESITA_REVISION"}
        and record.get("datos_extraidos_json")
    ]
    if not processed:
        st.info("Todavía no hay resultados OCR locales para inspeccionar.")
        return
    selected_id = st.selectbox(
        "Resultado a inspeccionar",
        options=[record["job_id"] for record in reversed(processed)],
        format_func=lambda job_id: next(
            (
                f"{record.get('nombre_original', '')} · {record.get('estado', '')} · {job_id[:8]}"
                for record in processed
                if record.get("job_id") == job_id
            ),
            job_id,
        ),
    )
    selected = next(record for record in processed if record["job_id"] == selected_id)
    try:
        payload = decode_json_from_sheet(selected["datos_extraidos_json"])
    except Exception:  # noqa: BLE001
        st.error("El checkpoint OCR no contiene JSON válido.")
        return

    first, second, third, fourth = st.columns(4)
    first.metric("Estado", selected.get("estado", ""))
    second.metric("Líneas", payload.get("line_count", 0))
    second_value = float(payload.get("mean_ocr_confidence", 0) or 0)
    third.metric("Confianza OCR", f"{second_value:.1%}")
    fourth.metric("Calidad más débil", payload.get("weakest_quality", ""))
    st.caption(f"Perfil: {payload.get('engine_profile', '')}")

    quality_rows = []
    line_rows = []
    qr_rows = []
    for page in payload.get("pages", []):
        quality = page.get("quality", {})
        quality_rows.append({"pagina": page.get("page"), **quality})
        qr = page.get("qr", {})
        if qr.get("detected"):
            qr_rows.append(
                {
                    "pagina": page.get("page"),
                    "valido": qr.get("valid"),
                    "fuente": qr.get("source_image"),
                    "campos": qr.get("fields"),
                    "advertencias": qr.get("warnings"),
                }
            )
        for line in page.get("lines", []):
            line_rows.append(
                {
                    "pagina": line.get("page"),
                    "texto": line.get("text"),
                    "confianza": line.get("confidence"),
                    "version": line.get("preprocessing_version"),
                    "coordenadas": line.get("coordinates"),
                }
            )
    st.markdown("#### Calidad")
    st.dataframe(quality_rows, hide_index=True, width="stretch")
    if qr_rows:
        st.markdown("#### QR")
        st.dataframe(qr_rows, hide_index=True, width="stretch")
    st.markdown("#### Texto OCR")
    if line_rows:
        st.dataframe(line_rows, hide_index=True, width="stretch")
        with st.expander("Ver texto continuo"):
            st.text("\n".join(row["texto"] for row in line_rows))
    else:
        st.error("No se reconocieron líneas; este documento requiere revisión o nueva foto.")


def render_local_ocr() -> None:
    st.subheader("OCR local en CPU")
    if not infrastructure_ready():
        st.warning("Completa la infraestructura antes de procesar.")
        return
    st.warning(
        "La primera ejecución descarga los modelos gratuitos y puede tardar varios minutos. "
        "Streamlit debe permanecer abierto durante el lote actual."
    )
    first, second, third = st.columns(3)
    if first.button("Probar carga del modelo", width="stretch"):
        try:
            bundle = get_ocr_engine()
            st.success(f"Modelo local preparado: {bundle.profile}.")
        except OCRModelLoadError as exc:
            st.error(str(exc))
    if second.button("Recuperar locks OCR vencidos", width="stretch"):
        recovered = recover_expired_ocr_jobs(
            get_credentials(),
            get_secret("GOOGLE_SHEET_ID"),
        )
        st.success(f"Jobs recuperados: {recovered}.")
    if third.button("Reintentar errores OCR", width="stretch"):
        retried = retry_local_ocr_errors(
            get_credentials(),
            get_secret("GOOGLE_SHEET_ID"),
        )
        st.success(f"Jobs devueltos a PENDIENTE: {retried}.")
    render_ocr_worker()
    st.divider()
    render_ocr_result_viewer()


def main() -> None:
    render_header()
    st.info(
        "Fase 4: calidad, QR, preprocesamiento adaptativo y PaddleOCR local en CPU. "
        "Gemini continúa desactivado y consume cero tokens."
    )
    setup_tab, upload_tab, queue_tab, ocr_tab = st.tabs(
        ["Preparar", "Cargar", "Cola", "OCR local"]
    )
    with setup_tab:
        render_setup()
    with upload_tab:
        render_upload()
    with queue_tab:
        render_queue()
    with ocr_tab:
        render_local_ocr()
    st.caption(f"{APP_NAME} · {APP_VERSION}")


if __name__ == "__main__":
    main()
