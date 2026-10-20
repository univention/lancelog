# SPDX-License-Identifier: AGPL-3.0-only

import io
import json
import sys
import threading

import pytest
from _pytest.capture import CaptureFixture


pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402

from lancelog import DataFormat, DataSectionOptions, logger, setup_logging  # noqa: E402


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
