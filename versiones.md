Registro de versiones

0.7.0 — Fase 7 — 2026-08-31

Nueva pestaña Revisión con diseño adaptable a computadora y celular.

Vista del original y comparación opcional con preprocesamiento A.

Texto OCR, confianza, fuente y semáforo por campo.

Edición humana para peajes, boletas y facturas con normalización determinista.

Validación bloqueante de RUC, DNI, placa, fecha, serie, JSON e importes.

Guardado de borrador sin confirmar ni mover el original.

Registro único de diferencias en CORRECCIONES con fuente HUMANO.

Rechazo con motivo y permanencia del original en OCR_ENTRADA.

Estado CONFIRMANDO, lock persistente, verificación del propietario y recuperación por vencimiento.

Upsert por job_id en PEAJES, BOLETAS o FACTURAS.

Renombrado y movimiento del mismo archivo a OCR_CONFIRMADOS, sin copiar ni borrar.

PDF compartido movido únicamente después de confirmar todos sus jobs lógicos.

Reintento seguro después de una interrupción sin duplicar la fila final.

Gemini permanece desactivado y utiliza cero tokens.

51 pruebas automáticas superadas; seis pestañas verificadas sin excepciones.

0.6.0 — Fase 6 — 2026-08-29

Candidatos de RUC emisor con validación de dígito verificador peruano.

Separación conservadora entre RUC emisor y documento del cliente.

Razón social buscada en encabezado, cerca del RUC y mediante diccionario.

Relación persistente RUC–razón social verificada.

Serie y número con conservación de ceros a la izquierda.

Sección de detalle convertida a items_json estructurado.

concepto_resumen generado localmente sin enviar el documento a Gemini.

Parser común validado para boletas y facturas.

Lectura activa de DICCIONARIOS y PLANTILLAS desde Google Sheets.

Palabras identificadoras y regiones normalizadas por proveedor.

PaddleOCR sobre recortes esperados solo cuando falta un campo.

Nueva pestaña web Conocimiento para inspeccionar reglas persistentes.

Gemini permanece desactivado y utiliza cero tokens.

41 pruebas automáticas superadas.

0.5.0 — Fase 5 — 2026-08-29

Extracción mediante candidatos para los once campos de peaje.

Puntuación 35 % OCR, 25 % formato, 20 % etiqueta, 10 % posición y 10 % consistencia.

Conflicto cuando dos valores diferentes quedan dentro del margen configurado.

Fechas reales, horas, series con ceros, placas peruanas y correcciones posicionales.

Importes procesados únicamente con Decimal.

Validación bloqueante de subtotal, IGV y total.

Subtotal calculado solamente como total menos IGV y marcado CALCULADO.

Confianza global limitada por el campo obligatorio más débil.

Recuperación OCR B/C activada también cuando faltan fecha, placa o total.

Trazabilidad de fuente, texto, coordenadas, página y candidatos alternativos.

Interfaz de inspección estructurada para peajes.

Gemini permanece desactivado y utiliza cero tokens.

34 pruebas automáticas superadas.

Corrección de despliegue de la versión 0.4.0

Se eliminó libglib2.0-0 de packages.txt porque en Streamlit Community Cloud exigía libffi7, un paquete no disponible en la imagen actual de despliegue.

Se añadió su reemplazo actual libglib2.0-0t64, que proporciona libgthread-2.0.so.0 para que OpenCV pueda importar cv2.

Se conservan libgomp1 y libgl1, requeridos por PaddleOCR y OpenCV en CPU.

0.4.0 — Fase 4 — 2026-08-28

PP-OCRv6 tiny en CPU, español y 2 hilos.

Perfil alternativo PP-OCRv5 mobile si falla la configuración principal.

Modelo compartido mediante st.cache_resource.

OpenCV para QR, perspectiva, calidad y preprocesamiento.

Versiones A, B y C ejecutadas adaptativamente.

Segmentación vertical de recibos largos con superposición.

Conservación de texto, confianza, coordenadas, página y versión.

Calidad BUENA, ACEPTABLE, DEFICIENTE o ILEGIBLE.

OCR omitido en páginas ILEGIBLES; Gemini continúa desactivado.

Descarga temporal desde Drive y borrado automático de la copia.

Worker secuencial de 1, 5 o 10 documentos.

Botón para detener después del documento actual.

Lock global y lease persistente básico por job.

Recuperación de leases vencidos y reintento exclusivo de la etapa OCR.

JSON grande comprimido para respetar el límite por celda de Sheets.

22 pruebas automáticas superadas.

0.3.0 — Fase 3 — 2026-08-28

Creación idempotente de OCR_ENTRADA y OCR_CONFIRMADOS.

Creación de las nueve pestañas persistentes en Google Sheets.

Encabezado completo de OCR_COLA y configuración inicial.

Carga directa de JPG, JPEG, PNG, WEBP y PDF a Google Drive.

Cámara separada para computadora o celular.

Límite de 10 archivos por envío y 20 MB por archivo.

Checkpoint SUBIENDO antes de Drive y PENDIENTE después de persistir.

Recuperación de cargas interrumpidas sin duplicar el original.

Detección exacta SHA-256 y advertencia visual mediante dHash.

PDF como documento único o una fila lógica por página, compartiendo un solo
drive_file_id.

Pruebas offline de hashes, archivos, PDF, modelos, estados y duplicados.

Gemini continúa desactivado.

0.2.0 — Fase 2 — 2026-08-28

OAuth 2.0 del propietario con refresh token.

Alcance mínimo drive.file.

Conexión con Google Drive API.

Conexión con Google Sheets API.

Creación idempotente de OCR_DOCUMENTAL_DB.

Verificación de variables sin revelar sus valores.

Gemini continúa desactivado.

0.1.0 — Fase 1 — 2026-08-28

Aplicación mínima de Streamlit.

Pantalla de comprobación del entorno.

Modo MOCK sin credenciales.

Gemini desactivado.

Configuración inicial de seguridad.

Preparación para despliegue con Python 3.11
