OCR Documental Web

Aplicación web para capturar, procesar, revisar y exportar peajes, boletas y
facturas. Se despliega en Streamlit Community Cloud y persiste los originales
en Google Drive y los estados/resultados en Google Sheets.

Estado final

Versión: 1.0.0

Estado: versión final operativa

Interfaz: Dashboard, Cargar, Procesar, Revisión, Resultados, Conocimiento y Configuración

Archivos: JPG, JPEG, PNG, WEBP y PDF; máximo 10 por envío

OCR principal: PaddleOCR local en CPU, con perfiles adaptativos

QR: lectura local y validación conservadora SUNAT

Documentos: peajes, boletas y facturas con candidatos, reglas y confianza

Revisión: original y procesado, edición, rechazo y confirmación idempotente

Persistencia: dos carpetas de Drive y nueve pestañas de Sheets

Exportación: Excel, resumen PDF y respaldo CSV comprimido

Gemini: opcional, manual, apagado por defecto y limitado

Pruebas: 61 casos automatizados sin credenciales reales

Privacidad y Gemini

El flujo normal no utiliza Gemini. Cuando el administrador lo habilita y pulsa
manualmente el botón en Revisión:

Se eligen únicamente campos ausentes o de baja confianza.

Se seleccionan líneas OCR relacionadas y sus vecinas.

No se envía la imagen ni el OCR completo.

La respuesta se exige en JSON estructurado.

La propuesta queda con fuente GEMINI, confianza 0.70 y revisión obligatoria.

Se registran modelo, campos y tokens en LOGS; nunca la API key.

Existen límites por sesión, por día, por salida y un bloqueo de emergencia.

Persistencia

Carpetas de Drive:

OCR_ENTRADA: originales pendientes o rechazados.

OCR_CONFIRMADOS: el mismo archivo original, renombrado y movido tras confirmar.

Pestañas de Google Sheets:

OCR_COLA

PEAJES

BOLETAS

FACTURAS

DICCIONARIOS

PLANTILLAS

CORRECCIONES

LOGS

CONFIGURACION

La carga, el OCR y la confirmación usan checkpoints. Repetir una confirmación no
crea otra fila ni mueve el archivo otra vez.

Secrets

Los valores reales se guardan solo en Streamlit Secrets. El repositorio contiene
únicamente .streamlit/secrets.toml.example:

GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""
GOOGLE_REFRESH_TOKEN = ""
GOOGLE_SHEET_ID = ""
DRIVE_INPUT_FOLDER_ID = ""
DRIVE_CONFIRMED_FOLDER_ID = ""
GEMINI_API_KEY = ""
APP_ALLOWED_EMAILS = ""
APP_ADMIN_EMAIL = ""

GEMINI_API_KEY puede quedar vacío. No se usa una cuenta de servicio para el
Drive personal y el alcance OAuth previsto es drive.file.

Flujo operativo

En Configuración, verificar Drive y las nueve pestañas.

En Cargar, seleccionar tipo documental y persistir el original.

En Procesar, ejecutar uno o más jobs con OCR local.

En Revisión, comparar, corregir y confirmar o rechazar.

En Resultados, filtrar y descargar Excel, PDF o CSV.

En Dashboard, consultar estados, importes y uso de Gemini.

La primera carga del OCR puede tardar porque descarga los modelos. El lote local
requiere mantener abierta la sesión de Streamlit; cada documento terminado queda
guardado antes de empezar el siguiente.

Desarrollo y validación

python -m pytest -q

Resultado verificado para esta entrega:

61 passed

Punto de entrada: app.py.
