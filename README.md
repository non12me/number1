# OCR Documental Web

Aplicación web para procesar peajes, boletas y facturas sin instalar programas
en la computadora.

## Estado actual

- Versión: 0.1.0
- Fase: 1 de 10
- Modo: MOCK
- Gemini: desactivado
- Persistencia: se configurará en las fases 2 y 3

## Ejecución

El proyecto está diseñado para ejecutarse en Streamlit Community Cloud con
Python 3.11. No requiere ejecución local.

## Seguridad

Nunca se deben guardar credenciales reales en el repositorio. El archivo
`.streamlit/secrets.toml` está excluido mediante `.gitignore`. En la Fase 2, las
credenciales reales se guardarán únicamente en el panel Secrets de Streamlit.

## Punto de entrada

`app.py`
