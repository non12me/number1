# OCR Documental Web

Aplicación web para procesar peajes, boletas y facturas sin instalar programas
en la computadora.

## Estado actual

- Versión: 0.2.0
- Fase: 2 de 10
- OAuth: cuenta personal del propietario con acceso offline
- Alcance Google: `drive.file`
- Google Drive API: prueba disponible
- Google Sheets API: prueba disponible
- Gemini: desactivado

## Seguridad

- No se usa una cuenta de servicio para escribir en el Drive personal.
- Las credenciales reales se guardan únicamente en Streamlit Secrets.
- El repositorio contiene solo `.streamlit/secrets.toml.example`.
- La aplicación nunca imprime client ID, client secret, refresh token ni IDs
  configurados.
- El alcance `drive.file` limita la aplicación a los archivos que crea o utiliza.

## Archivos incorporados en la Fase 2

- `google_auth.py`: credenciales OAuth y renovación del access token.
- `google_drive.py`: prueba de Drive y creación idempotente de la hoja base.
- `google_sheets.py`: prueba de acceso a la hoja configurada.

## Punto de entrada

`app.py`
