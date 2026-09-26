"""Optional OpenTelemetry logging and Phoenix tracing integration.

Telemetry is deliberately best-effort: application startup and requests must
remain healthy when no collector is configured or the optional SDK is absent.
Set ``PHOENIX_COLLECTOR_ENDPOINT`` for Phoenix traces and
``OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`` (or the generic OTLP endpoint) for
Grafana-compatible structured logs.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)
_logging_configured = False
_phoenix_configured = False
_logger_provider: Any = None
_tracer_provider: Any = None


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def capture_content() -> bool:
    """Return whether prompt/response content may be sent to telemetry."""
    return _truthy(os.getenv("EDGENTRAG_TELEMETRY_CAPTURE_CONTENT"))


def configure(service_name: str) -> None:
    """Configure optional Phoenix spans and OTLP logs once per process."""
    global _logger_provider, _logging_configured
    global _phoenix_configured, _tracer_provider

    resource_attrs = {"service.name": os.getenv("OTEL_SERVICE_NAME") or service_name}
    logs_endpoint = os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    generic_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if logs_endpoint is None and generic_endpoint:
        logs_endpoint = generic_endpoint.rstrip("/") + "/v1/logs"
    if logs_endpoint and not _logging_configured:
        try:
            from opentelemetry._logs import set_logger_provider
            from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                OTLPLogExporter,
            )
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
            from opentelemetry.sdk.resources import Resource

            resource = Resource.create(resource_attrs)
            provider = LoggerProvider(resource=resource)
            provider.add_log_record_processor(
                BatchLogRecordProcessor(OTLPLogExporter(endpoint=logs_endpoint))
            )
            set_logger_provider(provider)
            logging.getLogger().addHandler(
                LoggingHandler(level=logging.NOTSET, logger_provider=provider)
            )
        except Exception:  # pragma: no cover - depends on exporter configuration
            logger.exception("Could not configure OTLP log export")
        else:
            _logger_provider = provider
            _logging_configured = True

    phoenix_endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
    if phoenix_endpoint and not _phoenix_configured:
        try:
            from phoenix.otel import register

            provider = register(
                project_name=os.getenv("PHOENIX_PROJECT_NAME", "edgentrag"),
                endpoint=phoenix_endpoint,
                auto_instrument=False,
                batch=True,
            )
        except Exception:  # pragma: no cover - depends on exporter configuration
            logger.exception("Could not configure Phoenix tracing")
        else:
            _tracer_provider = provider
            _phoenix_configured = True


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Create a best-effort span, safe when OpenTelemetry is not installed."""
    try:
        from opentelemetry import trace
    except ImportError:
        yield None
        return

    tracer = trace.get_tracer("edgentrag")
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        yield current


def set_span_attributes(current: Any, **attributes: Any) -> None:
    """Set attributes on a span returned by :func:`span`."""
    if current is None:
        return
    for key, value in attributes.items():
        if value is not None:
            current.set_attribute(key, value)
