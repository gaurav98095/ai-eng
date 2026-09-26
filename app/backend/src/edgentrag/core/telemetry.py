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
_configured_services: set[str] = set()
_logging_configured = False


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def capture_content() -> bool:
    """Return whether prompt/response content may be sent to telemetry."""
    return _truthy(os.getenv("EDGENTRAG_TELEMETRY_CAPTURE_CONTENT"))


def configure(service_name: str) -> None:
    """Configure optional Phoenix spans and OTLP logs once per process."""
    global _logging_configured
    if service_name in _configured_services:
        return
    _configured_services.add(service_name)

    resource_attrs = {"service.name": os.getenv("OTEL_SERVICE_NAME", service_name)}
    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
    except ImportError:
        if os.getenv("PHOENIX_COLLECTOR_ENDPOINT") or os.getenv(
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"
        ):
            logger.warning("Telemetry SDK is not installed; telemetry is disabled")
        return

    resource = Resource.create(resource_attrs)
    if not _logging_configured:
        logs_endpoint = os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
        generic_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if logs_endpoint is None and generic_endpoint:
            logs_endpoint = generic_endpoint.rstrip("/") + "/v1/logs"
        if logs_endpoint:
            try:
                provider = LoggerProvider(resource=resource)
                provider.add_log_record_processor(
                    BatchLogRecordProcessor(OTLPLogExporter(endpoint=logs_endpoint))
                )
                set_logger_provider(provider)
                logging.getLogger().addHandler(
                    LoggingHandler(level=logging.NOTSET, logger_provider=provider)
                )
                _logging_configured = True
            except Exception:  # pragma: no cover
                logger.exception("Could not configure OTLP log export")

    phoenix_endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
    if phoenix_endpoint:
        try:
            from phoenix.otel import register

            register(
                project_name=os.getenv("PHOENIX_PROJECT_NAME", "edgentrag"),
                endpoint=phoenix_endpoint,
                auto_instrument=False,
                batch=True,
            )
        except Exception:  # pragma: no cover
            logger.exception("Could not configure Phoenix tracing")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Create a best-effort span, safe when OpenTelemetry is not installed."""
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("edgentrag")
        with tracer.start_as_current_span(name) as current:
            for key, value in attributes.items():
                if value is not None:
                    current.set_attribute(key, value)
            yield current
    except ImportError:
        yield None


def set_span_attributes(current: Any, **attributes: Any) -> None:
    """Set attributes on a span returned by :func:`span`."""
    if current is None:
        return
    for key, value in attributes.items():
        if value is not None:
            current.set_attribute(key, value)
