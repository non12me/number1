OCR Documental Web

Aplicación web para procesar peajes, boletas y facturas sin instalar programas
en la computadora.

Estado actual

Versión: 0.5.0

Fase: 5 de 10

OAuth: cuenta personal del propietario con acceso offline

Alcance Google: drive.file

Drive: originales persistentes en OCR_ENTRADA

Sheets: cola persistente y nueve pestañas idempotentes

Carga: JPG, JPEG, PNG, WEBP y PDF; máximo 10 archivos por envío

Duplicados: SHA-256 exacto y advertencia mediante hash perceptual

PDF: un job por documento lógico sin duplicar el original

Cámara móvil: disponible mediante st.camera_input

Calidad: resolución, desenfoque, brillo, contraste, giro, perspectiva,
área documental y bordes cortados

Preprocesamiento: versiones A, B y C ejecutadas de forma adaptativa

QR: lectura local y validación conservadora del formato SUNAT

OCR: PP-OCRv6 tiny en CPU con alternativa PP-OCRv5 mobile

Worker: lotes secuenciales de 1, 5 o 10 con checkpoint por documento

Peajes: candidatos múltiples, normalización, validaciones y confianza por campo

Confianza global: limitada por lugar, fecha, placa y monto_total

Importes: Decimal, consistencia subtotal + IGV = total y cálculo trazable

Gemini: desactivado

Seguridad

No se usa una cuenta de servicio para escribir en el Drive personal.

Las credenciales reales se guardan únicamente en Streamlit Secrets.

El repositorio contiene solo .streamlit/secrets.toml.example.

La aplicación nunca imprime client ID, client secret, refresh token ni IDs
configurados.

El alcance drive.file limita la aplicación a los archivos que crea o utiliza.

Archivos incorporados o ampliados en la Fase 5

candidate_extractor.py: conserva todos los candidatos y su evidencia.

validators.py: fechas, horas, placas, serie, DNI, RUC e importes.

confidence.py: pesos configurables, conflictos y confianza global mínima.

parsers.py: selección conservadora y extracción completa de peajes.

document_processor.py: activa recuperación A/B/C cuando faltan campos.

app.py: muestra datos, fuente, confianza, alertas y candidatos alternativos.

tests/: 34 pruebas sin credenciales ni documentos reales.

Flujo de persistencia

La aplicación valida extensión, firma binaria y tamaño.

Calcula SHA-256 y, para imágenes, un dHash de 64 bits.

Relee OCR_COLA para impedir duplicados exactos.

Escribe un checkpoint SUBIENDO en Sheets.

Sube el original una sola vez a OCR_ENTRADA.

Guarda drive_file_id y cambia cada job a PENDIENTE.

Si Streamlit se reinicia entre los pasos 5 y 6, el botón de recuperación busca
el archivo mediante su SHA-256 privado y completa el checkpoint sin volver a
subirlo.

Flujo OCR y peajes de la Fase 5

El worker reclama un job PENDIENTE y lo marca PROCESANDO.

Descarga una copia temporal desde Drive.

Analiza la calidad y busca QR en original y perspectiva corregida.

Si es ILEGIBLE, omite OCR y solicita nueva fotografía.

Ejecuta versión A; B y C solo si la evidencia lo requiere.

Busca todos los candidatos de los campos de peaje y los puntúa.

Valida placa, fecha, hora, serie e importes sin suponer valores dudosos.

Guarda texto, candidatos, confianza, fuente y coordenadas en Sheets.

Elimina la copia temporal y deja el original intacto en Drive.

Marca EXTRAIDO_LOCAL, NECESITA_REVISION o ERROR.

Limitaciones deliberadas de esta fase

Boletas y facturas se incorporan en Fase 6.

La edición humana y confirmación se incorporan en Fase 7.

La primera ejecución debe descargar los pesos gratuitos del modelo.

El lote necesita una sesión de Streamlit abierta; no es una tarea permanente.

Gemini continúa apagado y no recibe imágenes ni texto.

Punto de entrada

app.py
