"""Telemetry configuration and failure-isolation tests."""

import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace

import pytest

from edgentrag.core import telemetry


def test_span_does_not_mask_import_error_from_instrumented_code(monkeypatch):
    fake_otel = ModuleType("opentelemetry")
    fake_otel.trace = SimpleNamespace(
        get_tracer=lambda _: SimpleNamespace(
            start_as_current_span=lambda _: nullcontext(
                SimpleNamespace(set_attribute=lambda *_: None)
            )
        )
    )
    monkeypatch.setitem(sys.modules, "opentelemetry", fake_otel)

    with pytest.raises(ImportError, match="domain dependency"):
        with telemetry.span("test"):
            raise ImportError("domain dependency")


def test_failed_phoenix_configuration_can_be_retried(monkeypatch):
    calls = 0

    def register(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary setup failure")
        return object()

    phoenix_package = ModuleType("phoenix")
    phoenix_otel = ModuleType("phoenix.otel")
    phoenix_otel.register = register
    monkeypatch.setitem(sys.modules, "phoenix", phoenix_package)
    monkeypatch.setitem(sys.modules, "phoenix.otel", phoenix_otel)
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix.test")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(telemetry, "_phoenix_configured", False)
    monkeypatch.setattr(telemetry, "_tracer_provider", None)

    telemetry.configure("test-service")
    telemetry.configure("test-service")

    assert calls == 2
    assert telemetry._phoenix_configured is True
    assert telemetry._tracer_provider is not None
