OCR Documental Web

Aplicación web para procesar peajes, boletas y facturas sin instalar programas
en la computadora.

Estado actual

Versión: 0.7.0

Fase: 7 de 10

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

Boletas y facturas: RUC emisor, razón social, serie, fecha, cliente e importes

Detalle: ítems JSON estructurados y concepto resumido sin Gemini

Conocimiento: diccionarios y plantillas activas desde Google Sheets

Revisión: original y preprocesamiento A lado a lado, texto OCR y campos editables

Correcciones: fuente HUMANO y registro trazable en CORRECCIONES

Confirmación: idempotente por job_id, con lock persistente y relectura de estado

Drive: renombra y mueve el mismo archivo a OCR_CONFIRMADOS, sin copiarlo

Rechazo: conserva el original en OCR_ENTRADA y registra el motivo

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

Archivos incorporados o ampliados en la Fase 7

review.py: campos editables, normalización y validación humana determinista.

confirmation.py: borrador, rechazo, lock, upsert y confirmación idempotente.

google_drive.py: vista previa en memoria y movimiento del mismo archivo.

google_sheets.py: upsert de resultados mediante job_id estable.

app.py: pestaña Revisión responsive con original, procesado, confianza y acciones.

tests/: 51 pruebas sin credenciales ni documentos reales.

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

Flujo OCR y documentos de la Fase 6

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

Para boletas y facturas se valida el RUC peruano, se separa el documento del
cliente, se reconstruye la sección de detalle y se aplica una relación
RUC–razón social solo si fue registrada como conocimiento activo. Cuando una
plantilla coincide y falta un campo, se ejecuta PaddleOCR únicamente en la
región configurada antes de solicitar revisión.

Flujo de revisión y confirmación de la Fase 7

La pestaña Revisión relee únicamente jobs con resultado estructurado pendiente.

El usuario compara el original, el preprocesamiento y el texto OCR.

Cada campo muestra confianza, fuente y color verde, amarillo o rojo.

Guardar correcciones actualiza el checkpoint y registra diferencias sin mover Drive.

Confirmar y mover vuelve a validar RUC, DNI, placa, fecha e importes.

El job pasa a CONFIRMANDO con propietario y vencimiento de lock.

El resultado se inserta o actualiza por job_id en su pestaña final.

Se renombra y mueve el mismo drive_file_id a OCR_CONFIRMADOS.

El checkpoint termina en CONFIRMADO; repetir la acción no crea otra fila.

Si un PDF comparte varios jobs, se mueve solo al confirmar el último.

Limitaciones deliberadas de esta fase

Las correcciones quedan registradas, pero no crean reglas automáticamente sin aprobación.

Gemini opcional, sus límites y el contador de tokens se incorporan en Fase 8.

La primera ejecución debe descargar los pesos gratuitos del modelo.

El lote necesita una sesión de Streamlit abierta; no es una tarea permanente.

Gemini continúa apagado y no recibe imágenes ni texto.

Punto de entrada

app.py
