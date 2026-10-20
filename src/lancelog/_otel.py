# SPDX-License-Identifier: AGPL-3.0-only

"""OpenTelemetry integration. Imported only when an OpenTelemetry option is enabled."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

import loguru
from loguru import logger
from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.context import _SUPPRESS_INSTRUMENTATION_KEY, Context, get_value
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from .setup import OtelOptions


_SEVERITY_BY_LEVEL_NAME = {
    "TRACE": SeverityNumber.TRACE,
    "DEBUG": SeverityNumber.DEBUG,
    "INFO": SeverityNumber.INFO,
    "SUCCESS": SeverityNumber.INFO2,
    "WARNING": SeverityNumber.WARN,
    "ERROR": SeverityNumber.ERROR,
    "CRITICAL": SeverityNumber.FATAL,
}
_EXCLUDED_ATTRIBUTES = {"serialized", "_traceback"}
_TRACE_ATTRIBUTES = {"trace_id", "span_id", "trace_flags"}
_PRIMITIVES = (str, bool, int, float)


def trace_context() -> dict[str, str]:
    """Return trace_id, span_id and trace_flags of the active span, or an empty dict if there is none."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "trace_flags": format(int(ctx.trace_flags), "02x"),
    }


def _severity(name: str, no: int) -> SeverityNumber:
    if name in _SEVERITY_BY_LEVEL_NAME:
        return _SEVERITY_BY_LEVEL_NAME[name]
    if no < 10:
        return SeverityNumber.TRACE
    if no < 20:
        return SeverityNumber.DEBUG
    if no < 30:
        return SeverityNumber.INFO
    if no < 40:
        return SeverityNumber.WARN
    if no < 50:
        return SeverityNumber.ERROR
    return SeverityNumber.FATAL


def _attribute_value(value: Any) -> Any:
    """Keep primitives and homogeneous sequences of primitives, convert anything else with `str`."""
    if isinstance(value, _PRIMITIVES):
        return value
    if (
        isinstance(value, list | tuple)
        and len({type(v) for v in value}) <= 1
        and all(isinstance(v, _PRIMITIVES) for v in value)
    ):
        return tuple(value)
    return str(value)


def _context(extra: dict[str, Any]) -> Context | None:
    if "trace_id" not in extra or "span_id" not in extra:
        return None
    try:
        span_context = SpanContext(
            int(extra["trace_id"], 16),
            int(extra["span_id"], 16),
            is_remote=False,
            trace_flags=TraceFlags(int(extra.get("trace_flags", "00"), 16)),
        )
    except (ValueError, TypeError):  # application-bound values that are not hex IDs
        return None
    return trace.set_span_in_context(NonRecordingSpan(span_context))


def _exclude_opentelemetry(record: loguru.Record) -> bool:
    return not record["name"].startswith("opentelemetry") if record["name"] else True


def add_otlp_sink(options: OtelOptions, source_ref_key: str) -> Callable[[], None]:
    """
    Add a Loguru sink that exports records over OTLP/HTTP.

    The provider is private: it is neither set as the global provider nor is a global one reused.

    :return: A function that removes the sink, flushes pending records and shuts the provider down.
    """
    import lancelog

    resource_attributes: dict[str, Any] = dict(options.resource_attributes or {})
    if options.service_name is not None:
        resource_attributes["service.name"] = options.service_name
    exporter_kwargs = {
        key: value
        for key, value in (("endpoint", options.endpoint), ("headers", options.headers), ("timeout", options.timeout))
        if value is not None
    }
    provider = LoggerProvider(resource=Resource.create(resource_attributes), shutdown_on_exit=False)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(**exporter_kwargs)))
    otel_logger = provider.get_logger("lancelog", lancelog.__version__)
    excluded = _EXCLUDED_ATTRIBUTES | {source_ref_key}
    excluded_with_trace = excluded | _TRACE_ATTRIBUTES

    def sink(message: Any) -> None:
        if get_value(_SUPPRESS_INSTRUMENTATION_KEY):
            return
        record = message.record
        extra = record["extra"]
        context = _context(extra)
        skip = excluded_with_trace if context else excluded
        attributes = {k: _attribute_value(v) for k, v in extra.items() if k not in skip and v is not None}
        attributes["code.file.path"] = record["file"].path
        attributes["code.function.name"] = (
            f"{record['name']}.{record['function']}" if record["name"] else record["function"]
        )
        attributes["code.line.number"] = record["line"]
        exception = record["exception"]
        otel_logger.emit(
            timestamp=round(record["time"].timestamp() * 1_000_000) * 1000,
            observed_timestamp=time.time_ns(),
            context=context,
            severity_number=_severity(record["level"].name, record["level"].no),
            severity_text=record["level"].name,
            body=record.get("raw_message", record["message"]),
            attributes=attributes,
            exception=exception.value if exception else None,
        )

    level = getattr(options.level, "value", options.level)
    handler_id = logger.add(sink, level=level, filter=_exclude_opentelemetry, format="{message}")

    def shutdown() -> None:
        with contextlib.suppress(ValueError):  # the application may have removed it already
            logger.remove(handler_id)
        provider.shutdown()

    return shutdown
