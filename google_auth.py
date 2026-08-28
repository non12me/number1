"""Construcción segura de credenciales OAuth 2.0 de Google."""

from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = ("https://www.googleapis.com/auth/drive.file",)


class GoogleConfigurationError(RuntimeError):
    """Indica que falta una variable OAuth obligatoria."""


def get_google_credentials(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> Credentials:
    """Crea y refresca credenciales sin registrar ningún secreto."""
    missing = []
    if not client_id:
        missing.append("GOOGLE_CLIENT_ID")
    if not client_secret:
        missing.append("GOOGLE_CLIENT_SECRET")
    if not refresh_token:
        missing.append("GOOGLE_REFRESH_TOKEN")

    if missing:
        raise GoogleConfigurationError(
            "Faltan estas variables: " + ", ".join(missing)
        )

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=list(GOOGLE_SCOPES),
    )
    credentials.refresh(Request())

    if not credentials.valid:
        raise GoogleConfigurationError(
            "Google no devolvió credenciales válidas."
        )

    return credentials
