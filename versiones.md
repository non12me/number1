# Registro de versiones

## 0.4.0 — Fase 4 — 2026-08-28

- PP-OCRv6 tiny en CPU, español y 2 hilos.
- Perfil alternativo PP-OCRv5 mobile si falla la configuración principal.
- Modelo compartido mediante `st.cache_resource`.
- OpenCV para QR, perspectiva, calidad y preprocesamiento.
- Versiones A, B y C ejecutadas adaptativamente.
- Segmentación vertical de recibos largos con superposición.
- Conservación de texto, confianza, coordenadas, página y versión.
- Calidad BUENA, ACEPTABLE, DEFICIENTE o ILEGIBLE.
- OCR omitido en páginas ILEGIBLES; Gemini continúa desactivado.
- Descarga temporal desde Drive y borrado automático de la copia.
- Worker secuencial de 1, 5 o 10 documentos.
- Botón para detener después del documento actual.
- Lock global y lease persistente básico por job.
- Recuperación de leases vencidos y reintento exclusivo de la etapa OCR.
- JSON grande comprimido para respetar el límite por celda de Sheets.
- 22 pruebas automáticas superadas.

## 0.3.0 — Fase 3 — 2026-08-28

- Creación idempotente de `OCR_ENTRADA` y `OCR_CONFIRMADOS`.
- Creación de las nueve pestañas persistentes en Google Sheets.
- Encabezado completo de `OCR_COLA` y configuración inicial.
- Carga directa de JPG, JPEG, PNG, WEBP y PDF a Google Drive.
- Cámara separada para computadora o celular.
- Límite de 10 archivos por envío y 20 MB por archivo.
- Checkpoint `SUBIENDO` antes de Drive y `PENDIENTE` después de persistir.
- Recuperación de cargas interrumpidas sin duplicar el original.
- Detección exacta SHA-256 y advertencia visual mediante dHash.
- PDF como documento único o una fila lógica por página, compartiendo un solo
  `drive_file_id`.
- Pruebas offline de hashes, archivos, PDF, modelos, estados y duplicados.
- Gemini continúa desactivado.

## 0.2.0 — Fase 2 — 2026-08-28

- OAuth 2.0 del propietario con refresh token.
- Alcance mínimo `drive.file`.
- Conexión con Google Drive API.
- Conexión con Google Sheets API.
- Creación idempotente de `OCR_DOCUMENTAL_DB`.
- Verificación de variables sin revelar sus valores.
- Gemini continúa desactivado.

## 0.1.0 — Fase 1 — 2026-08-28

- Aplicación mínima de Streamlit.
- Pantalla de comprobación del entorno.
- Modo MOCK sin credenciales.
- Gemini desactivado.
- Configuración inicial de seguridad.
- Preparación para despliegue con Python 3.11.
