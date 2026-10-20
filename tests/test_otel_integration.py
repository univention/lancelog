# SPDX-License-Identifier: AGPL-3.0-only

import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest


pytest.importorskip("opentelemetry.sdk")

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (  # noqa: E402
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue  # noqa: E402


CHILD_TIMEOUT = 30

DELIVERY_SCRIPT = """
import sys
from opentelemetry.sdk.trace import TracerProvider
from lancelog import DataFormat, DataSectionOptions, OtelOptions, logger, setup_logging

setup_logging(
    data_format=DataFormat.JSON,
    data_options=DataSectionOptions(trace_context=True),
    otel=OtelOptions(endpoint=sys.argv[1], level="TRACE", service_name="it"),
)
with TracerProvider().get_tracer("it").start_as_current_span("s") as span:
    ctx = span.get_span_context()
    print(format(ctx.trace_id, "032x"), format(ctx.span_id, "016x"), file=sys.stderr)
    logger.trace("trace record")
    logger.success("success record")
    logger.bind(k="v", n=3).info("bound record")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("exception record")
    logger.info("last")
"""

UNREACHABLE_SCRIPT = """
import atexit
import sys
import time
from opentelemetry.sdk._logs._internal import Logger
from lancelog import OtelOptions, logger, setup_logging

emitted = []
original_emit = Logger.emit


def counting_emit(self, *args, **kwargs):
    emitted.append(kwargs.get("body"))
    return original_emit(self, *args, **kwargs)


Logger.emit = counting_emit
atexit.register(lambda: print("emitted", len(emitted), file=sys.stderr))
setup_logging(sink=sys.stdout, otel=OtelOptions(endpoint=sys.argv[1], timeout=1))
for i in range(20):
    logger.info("record {}", i)
    time.sleep(0.05)
print("done")
"""


class _Handler(BaseHTTPRequestHandler):
    server: "_Receiver"

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        request = ExportLogsServiceRequest()
        request.ParseFromString(body)
        self.server.requests.append(request)
        payload = ExportLogsServiceResponse().SerializeToString()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        pass


class _Receiver(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[ExportLogsServiceRequest] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/v1/logs"

    def records(self) -> list[Any]:
        return [
            record
            for request in self.requests
            for resource_logs in request.resource_logs
            for scope_logs in resource_logs.scope_logs
            for record in scope_logs.log_records
        ]


@pytest.fixture  # type: ignore
def receiver() -> Iterator[_Receiver]:
    server = _Receiver()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def _run_child(script: str, endpoint: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), endpoint],
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT,
        env={**{k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}, **env},
    )


def _attributes(record: Any) -> dict[str, Any]:
    return {kv.key: AnyValue.ListFields(kv.value)[0][1] for kv in record.attributes}


def test_records_arrive_including_last_before_exit(receiver: _Receiver) -> None:
    result = _run_child(DELIVERY_SCRIPT, receiver.url, OTEL_BLRP_SCHEDULE_DELAY="60000")

    assert result.returncode == 0, result.stderr
    trace_id, span_id = result.stderr.split()[-2:]
    records = receiver.records()
    assert [r.body.string_value for r in records if r.body.string_value != "Configured logging."] == [
        "trace record",
        "success record",
        "bound record",
        "exception record",
        "last",
    ]
    by_body = {r.body.string_value: r for r in records}
    assert [by_body[b].severity_number for b in ("trace record", "success record", "bound record")] == [1, 10, 9]
    assert by_body["exception record"].severity_number == 17
    assert _attributes(by_body["bound record"])["k"] == "v"
    assert _attributes(by_body["bound record"])["n"] == 3
    exception = _attributes(by_body["exception record"])
    assert exception["exception.type"] == "ValueError"
    assert exception["exception.message"] == "boom"
    assert "ValueError" in exception["exception.stacktrace"]
    for body in ("trace record", "last"):
        assert by_body[body].trace_id.hex() == trace_id
        assert by_body[body].span_id.hex() == span_id
    resource = receiver.requests[0].resource_logs[0].resource
    assert {kv.key: kv.value.string_value for kv in resource.attributes}["service.name"] == "it"


def test_unreachable_endpoint() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    start = time.monotonic()
    result = _run_child(UNREACHABLE_SCRIPT, f"http://127.0.0.1:{port}/v1/logs")
    duration = time.monotonic() - start

    assert result.returncode == 0, result.stderr
    assert duration < 20
    assert all(f"record {i}" in result.stdout for i in range(20))
    assert "done" in result.stdout
    assert "Failed to export logs batch" in result.stdout
    emitted = int(result.stderr.split("emitted")[-1].split()[0])
    assert emitted == 20 + result.stdout.count("Configured logging.")
