"""Aplicación final OCR: captura, extracción, revisión y reportes."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone
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
    GEMINI_ENABLED,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MAX_REQUESTS_PER_DAY,
    GEMINI_MAX_REQUESTS_PER_SESSION,
    GEMINI_MODEL,
    PROJECT_PHASE,
    REPORT_MAX_ROWS,
)
from confirmation import confirm_review, reject_review, save_review_draft, suggested_filename
from gemini_fallback import recover_job_with_gemini
from google_auth import get_google_credentials
from google_drive import download_file_bytes, ensure_storage_folders, validate_folder
from google_sheets import (
    configuration_map,
    ensure_workbook_structure,
    read_multiple_records,
    read_records,
    upsert_record,
)
from local_ocr import OCRModelLoadError, get_ocr_engine
from models import DocumentType
from ocr_worker import (
    process_one_pending_job,
    recover_expired_ocr_jobs,
    retry_local_ocr_errors,
)
from queue_manager import enqueue_upload, recover_upload_checkpoints
from reports import (
    build_dashboard_metrics,
    filter_rows,
    make_csv_zip,
    make_excel_report,
    make_pdf_summary,
    monthly_expenses,
    type_summary,
)
from review import FIELD_LABELS, build_review_previews, field_names, values_from_payload
from upload_manager import prepare_upload
from utils import decode_json_from_sheet, pdf_page_count


st.set_page_config(
    page_title=APP_NAME,
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container {max-width: 1500px; padding-top: 1.25rem; padding-bottom: 2rem;}
    [data-testid="stMetric"] {background: #f8fafc; border: 1px solid #e2e8f0;
        border-radius: 12px; padding: .75rem 1rem;}
    [data-testid="stTabs"] button {font-weight: 650;}
    .final-banner {padding: .85rem 1rem; border-radius: 12px; color: #164e63;
        background: linear-gradient(90deg,#ecfeff,#f8fafc); border: 1px solid #a5f3fc;}
    </style>
    """,
    unsafe_allow_html=True,
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


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().casefold() in {"1", "true", "si", "sí", "yes", "activo"}


def _int_value(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def runtime_configuration(credentials: Any | None = None) -> dict[str, Any]:
    """Carga opciones públicas; una caída de Sheets conserva valores seguros."""
    defaults: dict[str, Any] = {
        "gemini_enabled": GEMINI_ENABLED,
        "gemini_emergency_stop": False,
        "gemini_model": GEMINI_MODEL,
        "gemini_max_requests_day": GEMINI_MAX_REQUESTS_PER_DAY,
        "gemini_max_output_tokens": GEMINI_MAX_OUTPUT_TOKENS,
    }
    if not infrastructure_ready():
        return defaults
    try:
        records = read_records(
            credentials or get_credentials(), get_secret("GOOGLE_SHEET_ID"), "CONFIGURACION"
        )
    except Exception:  # noqa: BLE001
        return defaults
    values = configuration_map(records)
    defaults.update(
        {
            "gemini_enabled": _bool_value(values.get("gemini_enabled"), GEMINI_ENABLED),
            "gemini_emergency_stop": _bool_value(values.get("gemini_emergency_stop")),
            "gemini_model": values.get("gemini_model") or GEMINI_MODEL,
            "gemini_max_requests_day": _int_value(
                values.get("gemini_max_requests_day"), GEMINI_MAX_REQUESTS_PER_DAY, 0, 500
            ),
            "gemini_max_output_tokens": _int_value(
                values.get("gemini_max_output_tokens"), GEMINI_MAX_OUTPUT_TOKENS, 32, 512
            ),
        }
    )
    return defaults


def _final_data() -> dict[str, list[dict[str, str]]]:
    return read_multiple_records(
        get_credentials(),
        get_secret("GOOGLE_SHEET_ID"),
        ["OCR_COLA", "PEAJES", "BOLETAS", "FACTURAS", "LOGS"],
    )


def render_header() -> None:
    st.title("📄 OCR documental")
    st.caption("Peajes, boletas y facturas · Drive + Sheets · revisión humana")
    first, second, third, fourth = st.columns(4)
    first.metric("Versión", APP_VERSION)
    second.metric("Fase", PROJECT_PHASE)
    third.metric("Carga máxima", f"{MAX_FILES_PER_UPLOAD} archivos")
    fourth.metric("OCR principal", "CPU local")
    st.markdown(
        "<div class='final-banner'>Versión final operativa: el OCR local es el flujo principal; "
        "Gemini es manual, limitado y permanece apagado inicialmente.</div>",
        unsafe_allow_html=True,
    )


def render_dashboard() -> None:
    st.subheader("Panel general")
    if not infrastructure_ready():
        st.info(
            "La aplicación final está instalada. Completa la configuración para mostrar "
            "los indicadores de tus documentos."
        )
        return
    try:
        data = _final_data()
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudo cargar el panel: {type(exc).__name__}.")
        return
    metrics = build_dashboard_metrics(data)
    first, second, third, fourth, fifth = st.columns(5)
    first.metric("Jobs", metrics["jobs_total"])
    second.metric("Por revisar", metrics["revision"])
    third.metric("Confirmados", metrics["confirmados"])
    fourth.metric("Errores", metrics["errores"])
    fifth.metric("Total PEN", f"S/ {metrics['total_pen']:.2f}")

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Documentos confirmados")
        st.dataframe(type_summary(data), hide_index=True, width="stretch")
    with right:
        st.markdown("#### Uso controlado de Gemini")
        gemini_rows = [
            {"indicador": "Solicitudes", "valor": metrics["gemini_requests"]},
            {"indicador": "Tokens de entrada", "valor": metrics["gemini_input_tokens"]},
            {"indicador": "Tokens de salida", "valor": metrics["gemini_output_tokens"]},
        ]
        st.dataframe(gemini_rows, hide_index=True, width="stretch")

    monthly = monthly_expenses(data)
    if monthly:
        st.markdown("#### Gastos mensuales confirmados en PEN")
        st.bar_chart(monthly, x="mes", y="total_pen")
    else:
        st.caption("El gráfico aparecerá cuando existan documentos confirmados con fecha y total.")


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
        st.error("Faltan credenciales o el ID de Google Sheets en Streamlit Secrets.")
        return

    st.info(
        "Este botón crea o recupera OCR_ENTRADA y OCR_CONFIRMADOS, y prepara "
        "las nueve pestañas de OCR_DOCUMENTAL_DB. Puede pulsarse nuevamente sin duplicarlas."
    )
    if st.button("Crear o verificar estructura final", type="primary", width="stretch"):
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
    columns = st.columns(7)
    columns[0].metric("Jobs totales", len(records))
    columns[1].metric("Pendientes", counts.get("PENDIENTE", 0))
    columns[2].metric("Procesando", counts.get("PROCESANDO", 0))
    columns[3].metric("OCR local", counts.get("EXTRAIDO_LOCAL", 0))
    columns[4].metric("Revisión", counts.get("NECESITA_REVISION", 0))
    columns[5].metric("Errores", counts.get("ERROR", 0))
    columns[6].metric("Confirmados", counts.get("CONFIRMADO", 0))

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

    extraction = payload.get("extraction") or {}
    if extraction:
        document_type = extraction.get("document_type", selected.get("tipo_documento", ""))
        st.markdown(f"#### Datos extraídos · {document_type}")
        extraction_columns = st.columns(3)
        extraction_columns[0].metric(
            "Confianza global",
            f"{float(extraction.get('global_confidence', 0) or 0):.1%}",
        )
        extraction_columns[1].metric(
            "Validación",
            "VÁLIDO" if extraction.get("valid") else "REVISAR",
        )
        extraction_columns[2].metric(
            "Gemini",
            "Utilizado" if selected.get("gemini_usado", "").upper() == "TRUE" else "No utilizado",
        )
        field_rows = []
        candidate_rows = []
        for field_name, field_data in (extraction.get("fields") or {}).items():
            display_value = field_data.get("value")
            if field_name == "items_json" and display_value:
                try:
                    display_value = f"{len(json.loads(display_value))} ítem(s) estructurado(s)"
                except (json.JSONDecodeError, TypeError):
                    display_value = "Ítems pendientes de revisión"
            field_rows.append(
                {
                    "campo": field_name,
                    "valor": display_value,
                    "confianza_final": field_data.get("confidence_final", 0),
                    "confianza_ocr": field_data.get("confidence_ocr", 0),
                    "fuente": field_data.get("source", "OCR"),
                    "advertencias": " | ".join(field_data.get("warnings") or []),
                    "pagina": field_data.get("page", ""),
                }
            )
            for candidate in field_data.get("candidates") or []:
                candidate_rows.append(
                    {
                        "campo": field_name,
                        "valor": candidate.get("value"),
                        "puntuacion": candidate.get("final_score", 0),
                        "texto_ocr": candidate.get("raw_text", ""),
                        "etiqueta": candidate.get("nearby_label", ""),
                        "fuente": candidate.get("source", "OCR"),
                        "pagina": candidate.get("page", ""),
                    }
                )
        st.dataframe(field_rows, hide_index=True, width="stretch")
        blocking = extraction.get("blocking_validations") or []
        if blocking:
            st.error("Validaciones bloqueantes: " + " | ".join(blocking))
        if candidate_rows:
            with st.expander("Ver candidatos alternativos y puntuaciones"):
                st.dataframe(candidate_rows, hide_index=True, width="stretch")

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


def render_knowledge() -> None:
    st.subheader("Diccionarios y plantillas")
    if not infrastructure_ready():
        st.warning("Completa la infraestructura antes de consultar el conocimiento.")
        return
    try:
        credentials = get_credentials()
        spreadsheet_id = get_secret("GOOGLE_SHEET_ID")
        dictionaries = read_records(credentials, spreadsheet_id, "DICCIONARIOS")
        templates = read_records(credentials, spreadsheet_id, "PLANTILLAS")
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudieron leer diccionarios o plantillas: {type(exc).__name__}.")
        return

    active_values = {"true", "1", "si", "sí", "activo", "yes"}
    active_dictionaries = [
        row
        for row in dictionaries
        if row.get("activo", "").strip().casefold() in active_values
    ]
    active_templates = [
        row
        for row in templates
        if row.get("activo", "").strip().casefold() in active_values
    ]
    first, second = st.columns(2)
    first.metric("Diccionarios activos", len(active_dictionaries))
    second.metric("Plantillas activas", len(active_templates))
    st.info(
        "Las correcciones no entrenan una IA automáticamente. Puedes convertirlas manualmente "
        "en variantes, relaciones RUC–proveedor o regiones reutilizables."
    )
    with st.expander("Formato admitido para regiones de plantilla"):
        st.code("0.10,0.05,0.90,0.25", language="text")
        st.caption(
            "Orden: x1, y1, x2, y2; valores entre 0 y 1 relativos al documento procesado."
        )
    st.markdown("#### DICCIONARIOS")
    if dictionaries:
        st.dataframe(dictionaries, hide_index=True, width="stretch")
    else:
        st.caption("Todavía no existen entradas. No es obligatorio crearlas para la primera prueba.")
    st.markdown("#### PLANTILLAS")
    if templates:
        st.dataframe(templates, hide_index=True, width="stretch")
    else:
        st.caption("Todavía no existen plantillas. El OCR local general seguirá funcionando.")


def _limited_export_data(
    data: dict[str, list[dict[str, str]]],
) -> dict[str, list[dict[str, str]]]:
    return {name: rows[-REPORT_MAX_ROWS:] for name, rows in data.items()}


def render_results() -> None:
    st.subheader("Resultados confirmados y exportaciones")
    if not infrastructure_ready():
        st.warning("Completa la infraestructura antes de consultar resultados.")
        return
    try:
        data = _final_data()
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudieron leer los resultados: {type(exc).__name__}.")
        return

    document_type = st.selectbox(
        "Tipo de resultado",
        options=["TODOS", "PEAJES", "BOLETAS", "FACTURAS"],
        key="result_type",
    )
    filter_column, from_column, to_column = st.columns([2, 1, 1])
    query = filter_column.text_input(
        "Buscar", placeholder="RUC, placa, proveedor, serie o archivo"
    )
    date_from = from_column.date_input("Desde", value=None, key="result_date_from")
    date_to = to_column.date_input("Hasta", value=None, key="result_date_to")
    selected_sheets = (
        ["PEAJES", "BOLETAS", "FACTURAS"]
        if document_type == "TODOS"
        else [document_type]
    )
    visible: list[dict[str, str]] = []
    for sheet in selected_sheets:
        for row in data.get(sheet, []):
            visible.append({"tipo": sheet, **row})
    visible = filter_rows(
        visible,
        query=query,
        date_from=date_from.isoformat() if isinstance(date_from, date) else "",
        date_to=date_to.isoformat() if isinstance(date_to, date) else "",
    )
    st.metric("Registros encontrados", len(visible))
    if visible:
        st.dataframe(visible[-REPORT_MAX_ROWS:], hide_index=True, width="stretch")
    else:
        st.info("No hay resultados confirmados con esos filtros.")

    export_data = _limited_export_data(data)
    with st.expander("Descargar respaldo o reporte", expanded=True):
        st.caption(
            f"Cada exportación incluye como máximo {REPORT_MAX_ROWS} filas recientes por pestaña."
        )
        try:
            excel = make_excel_report(export_data)
            pdf = make_pdf_summary(export_data)
            csv_zip = make_csv_zip(export_data)
        except Exception as exc:  # noqa: BLE001
            st.error(f"No se pudieron generar las exportaciones: {type(exc).__name__}.")
            return
        first, second, third = st.columns(3)
        first.download_button(
            "Descargar Excel",
            data=excel,
            file_name=f"ocr_documental_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
        second.download_button(
            "Descargar resumen PDF",
            data=pdf,
            file_name=f"resumen_ocr_{date.today().isoformat()}.pdf",
            mime="application/pdf",
            width="stretch",
        )
        third.download_button(
            "Descargar CSV (ZIP)",
            data=csv_zip,
            file_name=f"respaldo_ocr_{date.today().isoformat()}.zip",
            mime="application/zip",
            width="stretch",
        )


def _save_config_value(
    credentials: Any,
    key: str,
    value: Any,
    value_type: str,
    description: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    upsert_record(
        credentials,
        get_secret("GOOGLE_SHEET_ID"),
        "CONFIGURACION",
        "clave",
        key,
        {
            "clave": key,
            "valor": str(value).lower() if isinstance(value, bool) else str(value),
            "tipo": value_type,
            "descripcion": description,
            "updated_at": now,
            "updated_by": get_secret("APP_ADMIN_EMAIL") or "ADMIN_NO_CONFIGURADO",
        },
    )


def render_configuration() -> None:
    st.subheader("Configuración y diagnóstico")
    render_setup()
    st.divider()
    if not infrastructure_ready():
        return
    credentials = get_credentials()
    config = runtime_configuration(credentials)
    api_ready = bool(get_secret("GEMINI_API_KEY"))
    st.markdown("#### Gemini opcional")
    st.caption(
        "Permanece apagado por defecto. Solo se ejecuta al pulsar el botón en Revisión y "
        "solo recibe líneas OCR relacionadas con los campos seleccionados; nunca la imagen."
    )
    with st.form("gemini_configuration"):
        enabled = st.checkbox("Permitir solicitudes manuales", value=bool(config["gemini_enabled"]))
        emergency_stop = st.checkbox(
            "Bloqueo de emergencia", value=bool(config["gemini_emergency_stop"])
        )
        model = st.text_input("Modelo", value=str(config["gemini_model"]), disabled=True)
        daily_limit = st.number_input(
            "Solicitudes máximas por día", min_value=0, max_value=500,
            value=int(config["gemini_max_requests_day"]), step=1,
        )
        output_tokens = st.number_input(
            "Tokens máximos de salida", min_value=32, max_value=512,
            value=int(config["gemini_max_output_tokens"]), step=32,
        )
        save = st.form_submit_button("Guardar configuración", type="primary", width="stretch")
    if save:
        for key, value, value_type, description in (
            ("gemini_enabled", enabled, "bool", "Interruptor de Gemini"),
            ("gemini_emergency_stop", emergency_stop, "bool", "Bloqueo inmediato de Gemini"),
            ("gemini_model", model, "text", "Modelo estable de bajo consumo"),
            ("gemini_max_requests_day", int(daily_limit), "int", "Límite diario"),
            ("gemini_max_output_tokens", int(output_tokens), "int", "Salida máxima"),
        ):
            _save_config_value(credentials, key, value, value_type, description)
        st.success("Configuración guardada en Google Sheets.")
    status_columns = st.columns(3)
    status_columns[0].metric("API key", "Configurada" if api_ready else "No configurada")
    status_columns[1].metric("Solicitudes", "Permitidas" if enabled else "Apagadas")
    status_columns[2].metric("Emergencia", "BLOQUEADA" if emergency_stop else "Normal")

    with st.expander("Diagnóstico sin mostrar secretos"):
        diagnostics = [
            {"componente": "OAuth Google", "estado": "OK" if get_secret("GOOGLE_REFRESH_TOKEN") else "FALTA"},
            {"componente": "Google Sheets", "estado": "OK" if get_secret("GOOGLE_SHEET_ID") else "FALTA"},
            {"componente": "Drive entrada", "estado": "OK" if get_secret("DRIVE_INPUT_FOLDER_ID") else "FALTA"},
            {"componente": "Drive confirmados", "estado": "OK" if get_secret("DRIVE_CONFIRMED_FOLDER_ID") else "FALTA"},
            {"componente": "Gemini opcional", "estado": "OK" if api_ready else "SIN CLAVE"},
        ]
        st.dataframe(diagnostics, hide_index=True, width="stretch")


def _reviewable_records() -> list[dict[str, str]]:
    records = read_records(get_credentials(), get_secret("GOOGLE_SHEET_ID"), "OCR_COLA")
    allowed = {"EXTRAIDO_LOCAL", "NECESITA_REVISION", "PENDIENTE_RESULTADO"}
    return [
        record
        for record in records
        if record.get("estado") in allowed and record.get("datos_extraidos_json")
    ]


def _render_original_preview(record: dict[str, str]) -> None:
    st.markdown("#### Documento")
    cache_key = f"review_preview_{record['job_id']}"
    if st.button(
        "Cargar vista previa",
        key=f"load_preview_{record['job_id']}",
        width="stretch",
    ):
        try:
            for key in list(st.session_state):
                if key.startswith("review_preview_") and key != cache_key:
                    del st.session_state[key]
            original_bytes = download_file_bytes(
                get_credentials(), record.get("drive_file_id", "")
            )
            st.session_state[cache_key] = build_review_previews(
                original_bytes,
                record.get("mime_type", ""),
                record.get("pagina_pdf", ""),
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"No se pudo cargar la vista previa: {type(exc).__name__}.")
    previews = st.session_state.get(cache_key)
    if previews is None:
        st.caption("La vista se descarga bajo pedido; el original permanece en Drive.")
        return
    original, processed = previews
    st.image(
        original,
        caption=f"Original · {record.get('nombre_original', '')}",
        channels="BGR",
        width="stretch",
    )
    if st.checkbox(
        "Comparar con preprocesamiento A",
        key=f"show_processed_{record['job_id']}",
    ):
        st.image(
            processed,
            caption="Orientación, perspectiva y contraste usados por OCR",
            channels="BGR",
            width="stretch",
        )


def render_review() -> None:
    st.subheader("Revisión humana y confirmación")
    flash = st.session_state.pop("review_flash", "")
    if flash:
        st.success(flash)
    if not infrastructure_ready():
        st.warning("Completa la infraestructura antes de revisar documentos.")
        return
    try:
        records = _reviewable_records()
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudo cargar la revisión: {type(exc).__name__}.")
        return
    if not records:
        st.success("No hay documentos pendientes de revisión o confirmación.")
        return

    selected_id = st.selectbox(
        "Documento pendiente",
        options=[record["job_id"] for record in reversed(records)],
        format_func=lambda value: next(
            (
                f"{row.get('nombre_original', '')} · {row.get('tipo_documento', '')} · "
                f"{row.get('estado', '')}"
                for row in records
                if row["job_id"] == value
            ),
            value,
        ),
        key="review_job_id",
    )
    record = next(row for row in records if row["job_id"] == selected_id)
    try:
        payload = decode_json_from_sheet(record["datos_extraidos_json"])
        values = values_from_payload(payload, record.get("tipo_documento", ""))
    except Exception:  # noqa: BLE001
        st.error("El checkpoint de este documento no contiene JSON válido.")
        return

    extraction = payload.get("extraction") or {}
    confidence = float(extraction.get("global_confidence", 0) or 0)
    first, second, third = st.columns(3)
    first.metric("Estado", record.get("estado", ""))
    second.metric("Confianza global", f"{confidence:.1%}")
    third.metric(
        "Gemini",
        "Utilizado" if record.get("gemini_usado", "").upper() == "TRUE" else "No utilizado",
    )
    blocking = extraction.get("blocking_validations") or []
    if blocking:
        st.warning("Revisa: " + " | ".join(blocking))

    preview_column, form_column = st.columns([1, 1], gap="large")
    with preview_column:
        _render_original_preview(record)
        with st.expander("Texto OCR"):
            lines = [
                line.get("text", "")
                for page in payload.get("pages", [])
                for line in page.get("lines", [])
            ]
            st.text("\n".join(lines) if lines else "Sin texto reconocido.")

    with form_column:
        st.markdown("#### Datos editables")
        field_metadata = extraction.get("fields") or {}
        edited: dict[str, str] = {}
        with st.form(f"review_form_{record['job_id']}"):
            for field in field_names(record.get("tipo_documento", "")):
                metadata = field_metadata.get(field) or {}
                score = float(metadata.get("confidence_final", 0) or 0)
                source = metadata.get("source", "OCR")
                indicator = "🟢" if score >= 0.90 else "🟡" if score >= 0.75 else "🔴"
                label = f"{indicator} {FIELD_LABELS.get(field, field)}"
                help_text = f"Confianza {score:.1%} · Fuente {source}"
                if field == "items_json":
                    edited[field] = st.text_area(
                        label,
                        value=values.get(field, ""),
                        help=help_text,
                        key=f"edit_{record['job_id']}_{field}",
                        height=120,
                    )
                else:
                    edited[field] = st.text_input(
                        label,
                        value=values.get(field, ""),
                        help=help_text,
                        key=f"edit_{record['job_id']}_{field}",
                    )
            st.caption("Nombre sugerido: " + suggested_filename(record, edited))
            approve = st.checkbox(
                "He comparado los datos con el original y confirmo que son correctos",
                key=f"approve_{record['job_id']}",
            )
            save_column, confirm_column = st.columns(2)
            with save_column:
                save_pressed = st.form_submit_button(
                    "Guardar correcciones", width="stretch"
                )
            with confirm_column:
                confirm_pressed = st.form_submit_button(
                    "Confirmar y mover", type="primary", width="stretch"
                )

        if save_pressed:
            result = save_review_draft(
                get_credentials(),
                get_secret("GOOGLE_SHEET_ID"),
                record["job_id"],
                edited,
                get_secret("APP_ADMIN_EMAIL") or "ADMIN_NO_CONFIGURADO",
            )
            if result.get("status") == "GUARDADO":
                st.success(result["message"])
                if result.get("errors"):
                    st.warning("Pendiente: " + " | ".join(result["errors"]))
            else:
                st.warning(result.get("message", result.get("status", "No guardado")))

        if confirm_pressed:
            if not approve:
                st.error("Marca la confirmación de revisión humana antes de continuar.")
            else:
                with st.spinner("Confirmando sin duplicar resultados..."):
                    result = confirm_review(
                        get_credentials(),
                        get_secret("GOOGLE_SHEET_ID"),
                        get_secret("DRIVE_CONFIRMED_FOLDER_ID"),
                        record["job_id"],
                        edited,
                        get_secret("APP_ADMIN_EMAIL") or "ADMIN_NO_CONFIGURADO",
                    )
                if result.get("status") == "CONFIRMADO":
                    st.session_state["review_flash"] = result["message"]
                    st.rerun()
                elif result.get("status") == "VALIDACION_ERROR":
                    st.error("No se confirmó: " + " | ".join(result.get("errors", [])))
                else:
                    st.warning(result.get("message", result.get("status", "No completado")))

    with st.expander("Recuperar campos con Gemini (opcional)"):
        config = runtime_configuration()
        enabled = bool(config["gemini_enabled"]) and not bool(config["gemini_emergency_stop"])
        api_key = get_secret("GEMINI_API_KEY")
        if not enabled:
            st.info("Gemini está apagado. Puedes habilitar solicitudes manuales en Configuración.")
        elif not api_key:
            st.warning("Falta GEMINI_API_KEY en Streamlit Secrets.")
        else:
            field_metadata = extraction.get("fields") or {}
            available_fields = list(field_names(record.get("tipo_documento", "")))
            suggested_fields = [
                name
                for name in available_fields
                if not str((field_metadata.get(name) or {}).get("value") or "").strip()
                or float((field_metadata.get(name) or {}).get("confidence_final", 0) or 0) < 0.75
            ]
            selected_fields = st.multiselect(
                "Campos ausentes o dudosos",
                options=available_fields,
                default=suggested_fields,
                format_func=lambda name: FIELD_LABELS.get(name, name),
                key=f"gemini_fields_{record['job_id']}",
            )
            session_requests = int(st.session_state.get("gemini_session_requests", 0))
            st.caption(
                f"Sesión: {session_requests}/{GEMINI_MAX_REQUESTS_PER_SESSION}. "
                "La propuesta siempre queda pendiente de revisión humana."
            )
            disabled = not selected_fields or session_requests >= GEMINI_MAX_REQUESTS_PER_SESSION
            if st.button(
                "Solicitar propuesta de campos",
                disabled=disabled,
                key=f"gemini_request_{record['job_id']}",
                width="stretch",
            ):
                with st.spinner("Consultando solo las líneas OCR relevantes..."):
                    result = recover_job_with_gemini(
                        credentials=get_credentials(),
                        spreadsheet_id=get_secret("GOOGLE_SHEET_ID"),
                        job_id=record["job_id"],
                        api_key=api_key,
                        model=str(config["gemini_model"]),
                        requested_fields=selected_fields,
                        user_email=get_secret("APP_ADMIN_EMAIL") or "ADMIN_NO_CONFIGURADO",
                        daily_limit=int(config["gemini_max_requests_day"]),
                        max_output_tokens=int(config["gemini_max_output_tokens"]),
                    )
                if result.get("status") == "PROPUESTO":
                    st.session_state["gemini_session_requests"] = session_requests + 1
                    st.session_state["review_flash"] = result["message"]
                    st.rerun()
                else:
                    st.warning(result.get("message", "Gemini no produjo una propuesta."))

    with st.expander("Rechazar y solicitar nueva foto sin borrar el original"):
        with st.form(f"reject_{record['job_id']}"):
            reason = st.text_area("Motivo", key=f"reject_reason_{record['job_id']}")
            reject_pressed = st.form_submit_button(
                "Rechazar y solicitar nueva foto", width="stretch"
            )
        if reject_pressed:
            result = reject_review(
                get_credentials(),
                get_secret("GOOGLE_SHEET_ID"),
                record["job_id"],
                reason,
                get_secret("APP_ADMIN_EMAIL") or "ADMIN_NO_CONFIGURADO",
            )
            if result.get("status") == "RECHAZADO":
                st.success(result["message"])
            else:
                st.error(result.get("message", "No se pudo rechazar."))


def render_processing() -> None:
    queue_tab, ocr_tab = st.tabs(["Cola persistente", "Ejecutar OCR local"])
    with queue_tab:
        render_queue()
    with ocr_tab:
        render_local_ocr()


def main() -> None:
    render_header()
    dashboard_tab, upload_tab, process_tab, review_tab, results_tab, knowledge_tab, config_tab = st.tabs(
        [
            "Dashboard",
            "Cargar",
            "Procesar",
            "Revisión",
            "Resultados",
            "Conocimiento",
            "Configuración",
        ]
    )
    with dashboard_tab:
        render_dashboard()
    with upload_tab:
        render_upload()
    with process_tab:
        render_processing()
    with review_tab:
        render_review()
    with results_tab:
        render_results()
    with knowledge_tab:
        render_knowledge()
    with config_tab:
        render_configuration()
    st.caption(f"{APP_NAME} · {APP_VERSION}")


if __name__ == "__main__":
    main()
