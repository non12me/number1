"""Pruebas puras para validación, hashes y PDF."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from utils import (
    hamming_distance_hex,
    logical_page_labels,
    pdf_page_count,
    perceptual_hash_bytes,
    sanitize_filename,
    sha256_bytes,
    validate_upload,
)


def make_png(color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (64, 32), color=color).save(stream, format="PNG")
    return stream.getvalue()


def make_pdf(pages: int) -> bytes:
    document = pymupdf.open()
    for _ in range(pages):
        document.new_page(width=300, height=400)
    data = document.tobytes()
    document.close()
    return data


def test_sha256_is_deterministic() -> None:
    assert sha256_bytes(b"peaje") == sha256_bytes(b"peaje")
    assert sha256_bytes(b"peaje") != sha256_bytes(b"factura")


def test_validate_png_signature_and_extension() -> None:
    safe_name, mime_type = validate_upload("foto válida.png", make_png())
    assert safe_name == "foto válida.png"
    assert mime_type == "image/png"


def test_rejects_extension_that_does_not_match_content() -> None:
    with pytest.raises(ValueError, match="extensión no coincide"):
        validate_upload("imagen.jpg", make_png())


def test_sanitize_filename_removes_unsafe_characters() -> None:
    assert sanitize_filename("../Peaje: Lima?.JPG") == "Peaje Lima.jpg"
    assert sanitize_filename("...pdf") == "documento.pdf"


def test_perceptual_hash_is_stable() -> None:
    data = make_png((10, 20, 30))
    first = perceptual_hash_bytes(data, "image/png")
    second = perceptual_hash_bytes(data, "image/png")
    assert len(first) == 16
    assert first == second
    assert hamming_distance_hex(first, second) == 0


def test_pdf_does_not_get_perceptual_hash() -> None:
    assert perceptual_hash_bytes(make_pdf(1), "application/pdf") == ""


def test_pdf_page_count_and_logical_labels() -> None:
    data = make_pdf(3)
    assert pdf_page_count(data) == 3
    assert logical_page_labels(3, separate_pages=False) == ["1-3"]
    assert logical_page_labels(3, separate_pages=True) == ["1", "2", "3"]


def test_rejects_invalid_pdf() -> None:
    with pytest.raises(ValueError, match="dañado o protegido"):
        pdf_page_count(b"%PDF-contenido-invalido")
