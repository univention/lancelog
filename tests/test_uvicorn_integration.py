# SPDX-License-Identifier: AGPL-3.0-only

import asyncio
import io
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from unittest.mock import patch

import pytest
import uvicorn
from _pytest.capture import CaptureFixture

from lancelog import DataFormat, RequestTimingMiddleware, setup_logging, uvicorn_log_config
from lancelog.setup import _InterceptHandler
from lancelog.uvicorn_log import _UvicornAccessHandler


request_id: ContextVar[str] = ContextVar("request_id", default="-")

Scope = MutableMapping[str, Any]
Receive = Callable[[], Any]
Send = Callable[[MutableMapping[str, Any]], Any]


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    path = scope["path"]
    request_id.set("req-" + path.strip("/"))
    if path == "/boom":
        raise RuntimeError("boom")
    if path == "/slow":
        await asyncio.sleep(0.2)
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


def _get(port: int, path: str, headers: dict[str, str] | None = None) -> int:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code


@contextmanager
def _server(log_config: dict[str, Any], timed: bool = False) -> Iterator[int]:
    config = uvicorn.Config(
        RequestTimingMiddleware(app) if timed else app, log_config=log_config, lifespan="off", host="127.0.0.1", port=0
    )
    server = uvicorn.Server(config)
    # Server.run(sockets=...) skips the "Uvicorn running on" message; bind_socket() logs it.
    sock = config.bind_socket()
    port = sock.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
    assert not thread.is_alive()


def _records(buf: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line)["record"] for line in buf.getvalue().splitlines()]


def test_uvicorn_records_go_through_lancelog(capfd: CaptureFixture) -> None:
    buf = io.StringIO()
    setup_logging(sink=buf, level="DEBUG", data_format=DataFormat.JSON, request_id_func=request_id.get)
    with _server(uvicorn_log_config(health_paths=["/health"])) as port:
        assert _get(port, "/health") == 200
        assert _get(port, "/items?token=secret") == 200
        assert _get(port, "/boom") == 500

    output = buf.getvalue()
    records = _records(buf)
    captured = capfd.readouterr()
    for text in (captured.out, captured.err):
        assert "HTTP/1.1" not in text
        assert "Uvicorn running" not in text
        assert "Traceback" not in text

    messages = [r["message"] for r in records]
    for prefix in ("Started server process", "Uvicorn running on", "Finished server process"):
        assert any(m.startswith(prefix) for m in messages)

    def access(path: str) -> dict[str, Any]:
        (match,) = [r for r in records if r["message"] == "HTTP request." and r["extra"]["url.path"] == path]
        return match

    health = access("/health")
    assert health["level"]["name"] == "DEBUG"
    assert health["extra"]["request_id"] == "req-health"
    assert health["extra"]["http.response.status_code"] == 200
    assert health["name"].startswith("uvicorn.")

    items = access("/items")
    assert items["level"]["name"] == "INFO"
    assert "url.query" not in items["extra"]
    assert "secret" not in output

    assert all("http.server.request.duration" not in r["extra"] for r in records)
    assert access("/boom")["extra"]["http.response.status_code"] == 500
    assert access("/boom")["level"]["name"] == "ERROR"
    (error,) = [r for r in records if r["message"] == "Exception in ASGI application"]
    assert error["level"]["name"] == "ERROR"
    assert "RuntimeError: boom" in error["extra"]["_traceback"]


def test_uvicorn_access_record_has_request_duration() -> None:
    buf = io.StringIO()
    setup_logging(sink=buf, level="INFO", data_format=DataFormat.JSON)
    with _server(uvicorn_log_config(), timed=True) as port:
        assert _get(port, "/items") == 200
        assert _get(port, "/slow") == 200
        assert _get(port, "/boom") == 500

    records = _records(buf)

    def duration(path: str) -> float:
        (match,) = [r for r in records if r["message"] == "HTTP request." and r["extra"]["url.path"] == path]
        return float(match["extra"]["http.server.request.duration"])

    assert 0 <= duration("/items") < duration("/slow")
    assert 0.2 <= duration("/slow") < 5
    assert 0 <= duration("/boom") < 5


def test_uvicorn_request_headers_logged_at_trace() -> None:
    buf = io.StringIO()
    setup_logging(sink=buf, level="TRACE", data_format=DataFormat.JSON)
    with _server(uvicorn_log_config(log_request_headers=True)) as port:
        assert _get(port, "/items?token=secret", headers={"X-Test": "abc"}) == 200

    (trace,) = [r for r in _records(buf) if r["message"] == "HTTP request headers and query string."]
    assert trace["level"]["name"] == "TRACE"
    assert trace["name"].startswith("uvicorn.")
    assert trace["extra"]["url.path"] == "/items"
    assert trace["extra"]["url.query"] == "token=secret"
    headers = json.loads(trace["extra"]["http.request.headers"])
    assert headers["host"] == f"127.0.0.1:{port}"
    assert headers["x-test"] == "abc"


def test_uvicorn_selected_request_headers_logged_at_trace() -> None:
    buf = io.StringIO()
    setup_logging(sink=buf, level="TRACE", data_format=DataFormat.JSON)
    with _server(uvicorn_log_config(log_request_headers=["X-Test"])) as port:
        assert _get(port, "/items", headers={"X-Test": "abc"}) == 200

    (trace,) = [r for r in _records(buf) if r["message"] == "HTTP request headers and query string."]
    assert trace["extra"]["http.request.header.x-test"] == "abc"
    assert "http.request.header.host" not in trace["extra"]
    assert "http.request.headers" not in trace["extra"]


def test_uvicorn_logging_failure_does_not_fail_request(capfd: CaptureFixture) -> None:
    buf = io.StringIO()
    setup_logging(sink=buf, level="TRACE", data_format=DataFormat.JSON)
    with (
        patch("lancelog.uvicorn_log._request_header_fields", side_effect=RuntimeError("boom")),
        _server(uvicorn_log_config(log_request_headers=True)) as port,
    ):
        assert _get(port, "/items") == 200

    assert not [r for r in _records(buf) if r["message"] == "Exception in ASGI application"]
    assert "RuntimeError: boom" in capfd.readouterr().err


@pytest.mark.parametrize("use_colors", [True, False])  # type: ignore
def test_uvicorn_config_accepts_use_colors(use_colors: bool) -> None:
    setup_logging(sink=io.StringIO())
    uvicorn.Config(app, log_config=uvicorn_log_config(), use_colors=use_colors)

    assert isinstance(logging.getLogger("uvicorn.access").handlers[0], _UvicornAccessHandler)
    assert any(isinstance(h, _InterceptHandler) for h in logging.getLogger().handlers)


def test_uvicorn_loggers_fixed_at_info_unless_log_level_is_passed() -> None:
    buf = io.StringIO()
    setup_logging(sink=buf, level="TRACE", data_format=DataFormat.JSON)
    uvicorn.Config(app, log_config=uvicorn_log_config())
    assert logging.getLogger("uvicorn.error").getEffectiveLevel() == logging.INFO
    logging.getLogger("uvicorn.error").debug("probe default")
    assert "probe default" not in buf.getvalue()

    uvicorn.Config(app, log_config=uvicorn_log_config(), log_level="trace")
    logging.getLogger("uvicorn.error").log(5, "probe trace")
    (record,) = [r for r in _records(buf) if r["message"] == "probe trace"]
    assert record["level"]["name"] == "TRACE"
