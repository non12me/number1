# OCR Documental Web

Aplicación web para procesar peajes, boletas y facturas sin instalar programas
en la computadora.

## Estado actual

- Versión: 0.4.0
- Fase: 4 de 10
- OAuth: cuenta personal del propietario con acceso offline
- Alcance Google: `drive.file`
- Drive: originales persistentes en `OCR_ENTRADA`
- Sheets: cola persistente y nueve pestañas idempotentes
- Carga: JPG, JPEG, PNG, WEBP y PDF; máximo 10 archivos por envío
- Duplicados: SHA-256 exacto y advertencia mediante hash perceptual
- PDF: un job por documento lógico sin duplicar el original
- Cámara móvil: disponible mediante `st.camera_input`
- Calidad: resolución, desenfoque, brillo, contraste, giro, perspectiva,
  área documental y bordes cortados
- Preprocesamiento: versiones A, B y C ejecutadas de forma adaptativa
- QR: lectura local y validación conservadora del formato SUNAT
- OCR: PP-OCRv6 tiny en CPU con alternativa PP-OCRv5 mobile
- Worker: lotes secuenciales de 1, 5 o 10 con checkpoint por documento
- Gemini: desactivado

## Seguridad

- No se usa una cuenta de servicio para escribir en el Drive personal.
- Las credenciales reales se guardan únicamente en Streamlit Secrets.
- El repositorio contiene solo `.streamlit/secrets.toml.example`.
- La aplicación nunca imprime client ID, client secret, refresh token ni IDs
  configurados.
- El alcance `drive.file` limita la aplicación a los archivos que crea o utiliza.

## Archivos incorporados o ampliados en la Fase 4

- `quality.py`: métricas y clasificación BUENA/ACEPTABLE/DEFICIENTE/ILEGIBLE.
- `preprocessing.py`: páginas PDF, perspectiva, A/B/C y recibos largos.
- `qr_reader.py`: OpenCV QR y validación de RUC, fecha, serie e importes.
- `local_ocr.py`: modelo tiny cacheado, alternativa móvil y líneas trazables.
- `document_processor.py`: copia temporal de Drive, páginas y limpieza automática.
- `ocr_worker.py`: lock global, lease por job, checkpoints y reintentos de etapa.
- `tests/`: 22 pruebas sin credenciales ni documentos reales.

## Flujo de persistencia

1. La aplicación valida extensión, firma binaria y tamaño.
2. Calcula SHA-256 y, para imágenes, un dHash de 64 bits.
3. Relee `OCR_COLA` para impedir duplicados exactos.
4. Escribe un checkpoint `SUBIENDO` en Sheets.
5. Sube el original una sola vez a `OCR_ENTRADA`.
6. Guarda `drive_file_id` y cambia cada job a `PENDIENTE`.

Si Streamlit se reinicia entre los pasos 5 y 6, el botón de recuperación busca
el archivo mediante su SHA-256 privado y completa el checkpoint sin volver a
subirlo.

## Flujo OCR de la Fase 4

1. El worker reclama un job `PENDIENTE` y lo marca `PROCESANDO`.
2. Descarga una copia temporal desde Drive.
3. Analiza la calidad y busca QR en original y perspectiva corregida.
4. Si es `ILEGIBLE`, omite OCR y solicita nueva fotografía.
5. Ejecuta versión A; B y C solo si la evidencia lo requiere.
6. Guarda texto, confianza, coordenadas, página y versión en Sheets.
7. Elimina la copia temporal y deja el original intacto en Drive.
8. Marca `EXTRAIDO_LOCAL`, `NECESITA_REVISION` o `ERROR`.

## Limitaciones deliberadas de esta fase

- Todavía no se seleccionan campos de negocio ni se confirman documentos.
- Los candidatos, validadores y confianza por campo se incorporan en Fase 5.
- La primera ejecución debe descargar los pesos gratuitos del modelo.
- El lote necesita una sesión de Streamlit abierta; no es una tarea permanente.
- Gemini continúa apagado y no recibe imágenes ni texto.

## Punto de entrada

`app.py`
