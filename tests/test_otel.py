# SPDX-License-Identifier: AGPL-3.0-only

import atexit
import io
import json
import logging
import sys
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from _pytest.capture import CaptureFixture


pytest.importorskip("opentelemetry.sdk")

from opentelemetry._logs import SeverityNumber  # noqa: E402
from opentelemetry.context import _SUPPRESS_INSTRUMENTATION_KEY, attach, detach, set_value  # noqa: E402
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402

from lancelog import DataFormat, DataSectionOptions, OtelOptions, _otel, logger, setup, setup_logging  # noqa: E402


tracer = TracerProvider().get_tracer("test")
TRACE_OPTIONS = DataSectionOptions(trace_context=True)


def test_trace_ids_in_json(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stdout, data_format=DataFormat.JSON, data_options=TRACE_OPTIONS)
    with tracer.start_as_current_span("s") as span:
        ctx = span.get_span_context()
        logger.info("Hello")
    extra = json.loads(capsys.readouterr().out)["record"]["extra"]
    assert extra["trace_id"] == format(ctx.trace_id, "032x")
    assert extra["span_id"] == format(ctx.span_id, "016x")
    assert extra["trace_flags"] == format(int(ctx.trace_flags), "02x")
    assert int(extra["trace_flags"], 16) & 1  # sampled bit; SDK 1.45 also sets the random-trace-id bit (0x03)


def test_trace_ids_in_logfmt(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stdout, data_options=TRACE_OPTIONS)
    with tracer.start_as_current_span("s") as span:
        ctx = span.get_span_context()
        logger.info("Hello")
    out = capsys.readouterr().out
    assert f"trace_id={ctx.trace_id:032x}" in out
    assert f"span_id={ctx.span_id:016x}" in out


def test_trace_ids_absent_without_span(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stdout, data_format=DataFormat.JSON, data_options=TRACE_OPTIONS)
    logger.info("Hello")
    extra = json.loads(capsys.readouterr().out)["record"]["extra"]
    assert not {"trace_id", "span_id", "trace_flags"} & extra.keys()


def test_trace_ids_with_enqueue_multiple_threads() -> None:
    out = io.StringIO()
    setup_logging(
        sink=out,
        data_format=DataFormat.JSON,
        data_options=TRACE_OPTIONS,
        enqueue=True,
    )
    expected: dict[str, str] = {}

    def work(who: str) -> None:
        with tracer.start_as_current_span(who) as span:
            expected[who] = format(span.get_span_context().trace_id, "032x")
            logger.bind(who=who).info("Hello")

    threads = [threading.Thread(target=work, args=(who,)) for who in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    logger.complete()
    extras = [json.loads(m)["record"]["extra"] for m in out.getvalue().splitlines()]
    seen = {e["who"]: e["trace_id"] for e in extras if "who" in e}
    assert seen == expected
    assert len(set(expected.values())) == 2


def test_trace_context_disabled_by_default(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stdout, data_format=DataFormat.JSON)
    with tracer.start_as_current_span("s"):
        logger.info("Hello")
    assert "trace_id" not in capsys.readouterr().out


def test_trace_ids_via_stdlib_logging(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stdout, data_options=TRACE_OPTIONS)
    with tracer.start_as_current_span("s") as span:
        logging.getLogger("x").warning("Hello")
    assert f"trace_id={span.get_span_context().trace_id:032x}" in capsys.readouterr().out


class _RecordingExporter(InMemoryLogRecordExporter):  # type: ignore
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.is_shut_down = False

    def shutdown(self) -> None:
        self.is_shut_down = True
        super().shutdown()


class Exported:
    def __init__(self) -> None:
        self.exporters: list[_RecordingExporter] = []

    @property
    def exporter(self) -> _RecordingExporter:
        return self.exporters[-1]

    def readable(self) -> list[Any]:
        """Flush by shutting the sink down, then return the exported records."""
        setup._shutdown_otel()
        return list(self.exporter.get_finished_logs())

    def records(self) -> list[Any]:
        return [r.log_record for r in self.readable()]


@pytest.fixture  # type: ignore
def exported(monkeypatch: pytest.MonkeyPatch) -> Iterator[Exported]:
    state = Exported()

    def factory(**kwargs: Any) -> _RecordingExporter:
        state.exporters.append(_RecordingExporter(**kwargs))
        return state.exporters[-1]

    monkeypatch.setattr(_otel, "OTLPLogExporter", factory)
    yield state
    setup._shutdown_otel()


def _setup(**kwargs: Any) -> None:
    kwargs.setdefault("otel", OtelOptions(level="TRACE"))
    setup_logging(sink=sys.stdout, **kwargs)


@pytest.mark.parametrize(
    ("level", "number", "text"),
    [
        ("TRACE", SeverityNumber.TRACE, "TRACE"),
        ("DEBUG", SeverityNumber.DEBUG, "DEBUG"),
        ("INFO", SeverityNumber.INFO, "INFO"),
        ("SUCCESS", SeverityNumber.INFO2, "SUCCESS"),
        ("WARNING", SeverityNumber.WARN, "WARNING"),
        ("ERROR", SeverityNumber.ERROR, "ERROR"),
        ("CRITICAL", SeverityNumber.FATAL, "CRITICAL"),
        (15, SeverityNumber.DEBUG, "Level15"),
        (35, SeverityNumber.WARN, "Level35"),
    ],
)  # type: ignore
def test_export_severity_mapping(exported: Exported, level: str | int, number: SeverityNumber, text: str) -> None:
    _setup()
    logger.log(level, "m")
    (record,) = [r for r in exported.records() if r.body == "m"]
    assert record.severity_number == number
    assert record.severity_text == text


def test_export_severity_by_range(exported: Exported) -> None:
    _setup()
    for no, name in ((5, "T"), (25, "I"), (45, "E"), (60, "F")):
        logger.level(name, no=no)
        logger.log(name, name)
    got = {r.body: r.severity_number for r in exported.records() if r.body in "TIEF"}
    assert got == {
        "T": SeverityNumber.TRACE,
        "I": SeverityNumber.INFO,
        "E": SeverityNumber.ERROR,
        "F": SeverityNumber.FATAL,
    }


def test_export_body_is_unescaped_message(exported: Exported, capsys: CaptureFixture) -> None:
    _setup()
    message = "a\nb\t| c"
    logger.info(message)
    (record,) = [r for r in exported.records() if r.body == message]
    assert record.body == message
    assert "a\\nb" in capsys.readouterr().out


class _Obj:
    def __str__(self) -> str:
        return "obj-str"


def test_export_attributes(exported: Exported) -> None:
    _setup()
    line = sys._getframe().f_lineno + 1
    logger.bind(s="x", i=1, f=1.5, b=True, l=["a", "b"], d={"k": 1}, n=None, o=_Obj()).info("attrs")
    (record,) = [r for r in exported.records() if r.body == "attrs"]
    attrs = record.attributes
    assert (attrs["s"], attrs["i"], attrs["f"], attrs["b"]) == ("x", 1, 1.5, True)
    assert attrs["l"] == ("a", "b")
    assert attrs["d"] == "{'k': 1}"
    assert "n" not in attrs
    assert attrs["o"] == "obj-str"
    assert not {"serialized", "_source", "_traceback"} & attrs.keys()
    assert attrs["code.file.path"] == __file__
    assert attrs["code.function.name"] == f"{__name__}.test_export_attributes"
    assert attrs["code.line.number"] == line


@pytest.mark.parametrize("data_format", [DataFormat.LOGFMT, DataFormat.JSON])  # type: ignore
def test_export_request_id_without_data_section_option(
    exported: Exported, capsys: CaptureFixture, data_format: DataFormat
) -> None:
    _setup(data_format=data_format, request_id_func=lambda: "abcdefghijklmnop")
    logger.info("rid")
    (record,) = [r for r in exported.records() if r.body == "rid"]
    assert record.attributes["request_id"] == "abcdefghijklmnop"
    if data_format == DataFormat.LOGFMT:
        out = capsys.readouterr().out
        assert "[abcdefghij] rid" in out
        assert "request_id=" not in out


def test_export_request_id_without_request_id_func(exported: Exported) -> None:
    _setup()
    logger.info("rid")
    (record,) = [r for r in exported.records() if r.body == "rid"]
    assert record.attributes["request_id"] == "-"


def test_export_bound_request_id_overrides(exported: Exported) -> None:
    _setup(request_id_func=lambda: "from-func")
    logger.bind(request_id="bound").info("rid")
    (record,) = [r for r in exported.records() if r.body == "rid"]
    assert record.attributes["request_id"] == "bound"


def test_export_exception_attributes(exported: Exported) -> None:
    _setup()
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed")
    (record,) = [r for r in exported.records() if r.body == "failed"]
    assert record.attributes["exception.type"] == "ValueError"
    assert record.attributes["exception.message"] == "boom"
    assert "Traceback" in record.attributes["exception.stacktrace"]
    assert "_traceback" not in record.attributes


def test_export_trace_context(exported: Exported) -> None:
    _setup(data_options=TRACE_OPTIONS)
    with tracer.start_as_current_span("s") as span:
        ctx = span.get_span_context()
        logger.info("in span")
    logger.info("no span")
    records = {r.body: r for r in exported.records()}
    assert records["in span"].trace_id == ctx.trace_id
    assert records["in span"].span_id == ctx.span_id
    assert records["in span"].trace_flags == ctx.trace_flags
    assert not records["no span"].trace_id
    assert "trace_id" not in records["in span"].attributes


def test_export_level_independent_of_stdout_level(exported: Exported, capsys: CaptureFixture) -> None:
    _setup(level="WARNING", otel=OtelOptions(level="DEBUG"))
    logger.debug("dbg")
    assert [r.body for r in exported.records() if r.body == "dbg"] == ["dbg"]
    assert "dbg" not in capsys.readouterr().out

    _setup(level="DEBUG", otel=OtelOptions(level="ERROR"))
    logger.info("inf")
    assert "inf" in capsys.readouterr().out
    assert [r for r in exported.records() if r.body == "inf"] == []


def test_export_resource_and_exporter_arguments(exported: Exported, monkeypatch: pytest.MonkeyPatch) -> None:
    options = OtelOptions(
        endpoint="http://e/v1/logs",
        headers={"a": "b"},
        timeout=2,
        service_name="svc",
        resource_attributes={"deployment.environment": "test"},
    )
    _setup(otel=options)
    logger.info("x")
    assert exported.exporter.kwargs == {"endpoint": "http://e/v1/logs", "headers": {"a": "b"}, "timeout": 2}
    resource = exported.readable()[0].resource
    assert resource.attributes["service.name"] == "svc"
    assert resource.attributes["deployment.environment"] == "test"

    monkeypatch.setenv("OTEL_SERVICE_NAME", "envsvc")
    _setup(otel=OtelOptions())
    logger.info("x")
    assert exported.exporter.kwargs == {}
    assert exported.readable()[0].resource.attributes["service.name"] == "envsvc"


def _otel_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("OtelBatch")]


def test_setup_twice_shuts_down_previous_provider(exported: Exported) -> None:
    _setup()
    _setup()
    first, second = exported.exporters
    assert first.is_shut_down
    assert not second.is_shut_down
    assert len(_otel_threads()) == 1
    logger.info("after")
    assert [r.body for r in exported.records() if r.body == "after"] == ["after"]
    assert all(r.log_record.body != "after" for r in first.get_finished_logs())


def test_setup_without_otel_shuts_down_previous_provider(exported: Exported) -> None:
    _setup()
    _setup(otel=None)
    assert exported.exporter.is_shut_down
    assert not _otel_threads()


def test_records_from_opentelemetry_loggers_not_exported(exported: Exported, capsys: CaptureFixture) -> None:
    _setup()
    logger.patch(lambda r: r.update(name="opentelemetry.exporter")).error("from otel")
    logger.info("own")
    assert [r.body for r in exported.records() if r.body in ("from otel", "own")] == ["own"]
    assert "from otel" in capsys.readouterr().out


def test_records_under_suppressed_instrumentation_not_exported(exported: Exported, capsys: CaptureFixture) -> None:
    _setup()
    token = attach(set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
    try:
        logger.info("suppressed")
    finally:
        detach(token)
    logger.info("normal")
    assert [r.body for r in exported.records() if r.body in ("suppressed", "normal")] == ["normal"]
    assert "suppressed" in capsys.readouterr().out


def test_atexit_handler_flushes(exported: Exported) -> None:
    _setup()
    logger.info("pending")
    setup._shutdown_otel()
    assert exported.exporter.is_shut_down
    assert [r.log_record.body for r in exported.exporter.get_finished_logs() if r.log_record.body == "pending"]
    setup._shutdown_otel()  # no sink configured: no-op


def test_atexit_handler_registered_once(exported: Exported, monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[Any] = []
    monkeypatch.setattr(atexit, "register", registered.append)
    monkeypatch.setattr(setup, "_atexit_registered", False)
    _setup()
    _setup()
    assert registered == [setup._shutdown_otel]


def test_shutdown_after_application_removed_handlers(exported: Exported) -> None:
    _setup()
    logger.remove()
    setup._shutdown_otel()
    assert exported.exporter.is_shut_down


def test_invalid_otel_level_starts_no_exporter(exported: Exported) -> None:
    with pytest.raises(ValueError, match="BOGUS"):
        _setup(otel=OtelOptions(level="BOGUS"))
    assert not exported.exporters
    assert not _otel_threads()


def test_invalid_otel_level_keeps_previous_configuration(exported: Exported) -> None:
    _setup()
    with pytest.raises(ValueError, match="BOGUS"):
        _setup(otel=OtelOptions(level="BOGUS"))
    logging.getLogger("app").warning("still exported")
    assert len(exported.exporters) == 1
    assert "still exported" in [r.body for r in exported.records()]


def test_otel_options_repr_hides_headers() -> None:
    assert "secret" not in repr(OtelOptions(headers={"Authorization": "secret"}))


def test_export_invalid_bound_trace_ids(exported: Exported, capsys: CaptureFixture) -> None:
    _setup()
    logger.bind(trace_id="1-5759e988-bd862e3fe1be46a994272793", span_id="53995c3f42cd8ad8").info("bad ids")
    (record,) = [r for r in exported.records() if r.body == "bad ids"]
    assert not record.trace_id
    assert record.attributes["trace_id"] == "1-5759e988-bd862e3fe1be46a994272793"
    assert record.attributes["span_id"] == "53995c3f42cd8ad8"
    assert capsys.readouterr().err == ""


def test_export_uses_active_span_without_trace_context_option(exported: Exported) -> None:
    _setup()
    with tracer.start_as_current_span("s") as span:
        ctx = span.get_span_context()
        logger.info("in span")
    (record,) = [r for r in exported.records() if r.body == "in span"]
    assert (record.trace_id, record.span_id) == (ctx.trace_id, ctx.span_id)


def test_export_body_without_raw_message_falls_back_to_message(exported: Exported) -> None:
    _setup()
    logger.configure(patcher=lambda record: None)
    logger.info("plain")
    assert "plain" in [r.body for r in exported.records()]


def test_export_function_name_without_logger_name(exported: Exported) -> None:
    _setup()
    logger.patch(lambda record: record.update(name=None)).info("anon")
    (record,) = [r for r in exported.records() if r.body == "anon"]
    assert record.attributes["code.function.name"] == "test_export_function_name_without_logger_name"
