"""Pruebas del fallback manual sin llamadas de red."""

from __future__ import annotations

from types import SimpleNamespace

from gemini_fallback import (
    collect_relevant_lines,
    merge_gemini_fields,
    request_missing_fields,
)


def payload() -> dict:
    return {
        "pages": [{
            "lines": [
                {"text": "EMPRESA DE PRUEBA SAC"},
                {"text": "RUC 20100070970"},
                {"text": "texto no relacionado"},
                {"text": "TOTAL S/ 118.00"},
            ]
        }],
        "extraction": {
            "fields": {
                "ruc_emisor": {"value": "", "confidence_final": 0.2, "source": "OCR"},
                "monto_total": {"value": "118.00", "confidence_final": 0.95, "source": "OCR"},
            },
            "global_confidence": 0.2,
            "valid": False,
            "warnings": [],
        },
    }


def test_relevant_text_is_limited_to_requested_evidence() -> None:
    lines = collect_relevant_lines(payload(), ["ruc_emisor"], max_chars=100)
    assert "RUC 20100070970" in lines
    assert sum(len(line) + 1 for line in lines) <= 101


def test_request_uses_json_schema_and_returns_usage() -> None:
    captured = {}

    class Models:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                parsed={"fields": {"ruc_emisor": "20100070970"}},
                usage_metadata=SimpleNamespace(
                    prompt_token_count=91,
                    candidates_token_count=8,
                ),
            )

    result = request_missing_fields(
        api_key="test-key",
        model="gemini-test",
        document_type="FACTURA",
        requested_fields=["ruc_emisor"],
        payload=payload(),
        client_factory=lambda _key: SimpleNamespace(models=Models()),
    )
    assert result.status == "PROPUESTO"
    assert result.fields == {"ruc_emisor": "20100070970"}
    assert (result.input_tokens, result.output_tokens) == (91, 8)
    assert captured["config"]["response_mime_type"] == "application/json"
    assert captured["config"]["response_json_schema"]["additionalProperties"] is False


def test_request_never_runs_without_key() -> None:
    result = request_missing_fields(
        api_key="",
        model="gemini-test",
        document_type="FACTURA",
        requested_fields=["ruc_emisor"],
        payload=payload(),
        client_factory=lambda _key: (_ for _ in ()).throw(AssertionError("no call")),
    )
    assert result.status == "SIN_API_KEY"


def test_merge_only_changes_requested_fields_and_requires_review() -> None:
    merged = merge_gemini_fields(
        payload(),
        {"ruc_emisor": "20100070970", "monto_total": "999.00"},
        ["ruc_emisor"],
    )
    fields = merged["extraction"]["fields"]
    assert fields["ruc_emisor"]["source"] == "GEMINI"
    assert fields["ruc_emisor"]["confidence_final"] == 0.70
    assert fields["monto_total"]["value"] == "118.00"
    assert merged["extraction"]["valid"] is False
