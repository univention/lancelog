# SPDX-License-Identifier: AGPL-3.0-only

import io
import json
import logging
import logging.config
import re
import subprocess
import sys
from collections.abc import Iterable
from importlib.metadata import requires
from typing import Any
from unittest.mock import patch

import pytest
from _pytest.capture import CaptureFixture

from lancelog import DataFormat, LogLevel, logger, setup_logging, uvicorn_log_config
from lancelog.setup import ADR0010_REGEX, _InterceptHandler
from lancelog.uvicorn_log import _request_header_fields, _trace_enabled


ACCESS_FORMAT = '%s - "%s %s HTTP/%s" %d'


def _setup(
    level: str | LogLevel = LogLevel.INFO,
    data_format: DataFormat = DataFormat.LOGFMT,
    log_request_headers: bool | Iterable[str] = False,
    **kwargs: Any,
) -> None:
    setup_logging(sink=sys.stderr, level=level, data_format=data_format, **kwargs)
    logging.config.dictConfig(uvicorn_log_config(health_paths=["/health"], log_request_headers=log_request_headers))


def _access(client: str = "127.0.0.1:5000", path: str = "/items", status: int = 200) -> None:
    logging.getLogger("uvicorn.access").info(ACCESS_FORMAT, client, "GET", path, "1.1", status)


class _RequestResponseCycle:
    """Stands in for uvicorn's ``RequestResponseCycle``, whose ``send()`` writes the access record."""

    def __init__(self, headers: list[tuple[Any, ...]]) -> None:
        self.scope = {"type": "http", "headers": headers}

    def send(self, path: str = "/items") -> None:
        logging.getLogger("uvicorn.access").info(ACCESS_FORMAT, "127.0.0.1:5000", "GET", path, "1.1", 200)


HEADERS = [(b"host", b"example.org"), (b"Accept", b"*/*"), (b"x-multi", b"a"), (b"x-multi", b"b")]


def _parse(line: str) -> dict[str, str]:
    m = re.match(ADR0010_REGEX, line)
    if not m:
        raise AssertionError(f"Regex matching of log record failed: {line!r}")  # pragma: nocover
    return m.groupdict()


def test_access_record_logfmt(capsys: CaptureFixture) -> None:
    _setup()
    _access()
    parsed = _parse(capsys.readouterr().err)
    assert parsed["message"] == "HTTP request."
    assert parsed["level"] == "INFO"
    for token in (
        "client.address=127.0.0.1",
        "client.port=5000",
        "http.request.method=GET",
        "url.path=/items",
        "network.protocol.version=1.1",
        "http.response.status_code=200",
        "_source=test_uvicorn_log._access:",
    ):
        assert token in parsed["data"]


def test_access_record_json(capsys: CaptureFixture) -> None:
    _setup(data_format=DataFormat.JSON)
    _access()
    record = json.loads(capsys.readouterr().err)["record"]
    assert record["extra"]["http.request.method"] == "GET"
    assert record["extra"]["client.port"] == 5000
    assert record["extra"]["http.response.status_code"] == 200
    assert record["function"] == "_access"


def test_access_record_request_id(capsys: CaptureFixture) -> None:
    _setup(request_id_func=lambda: "abcdef1234567")
    _access()
    assert _parse(capsys.readouterr().err)["request_id"] == "abcdef1234"


def test_health_path_logged_at_debug(capsys: CaptureFixture) -> None:
    _setup(level="DEBUG")
    capsys.readouterr()
    _access(path="/health")
    assert _parse(capsys.readouterr().err)["level"] == "DEBUG"

    _setup(level="INFO")
    capsys.readouterr()
    _access(path="/health")
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "path, status, level",
    [
        ("/health", 200, "DEBUG"),
        ("/health", 299, "DEBUG"),
        ("/health", 300, "INFO"),
        ("/health", 404, "INFO"),
        ("/health", 499, "INFO"),
        ("/health", 500, "ERROR"),
        ("/health", 503, "ERROR"),
        ("/items", 200, "INFO"),
        ("/items", 299, "INFO"),
        ("/items", 404, "INFO"),
        ("/items", 499, "INFO"),
        ("/items", 500, "ERROR"),
        ("/items", 503, "ERROR"),
    ],
)  # type: ignore
def test_access_record_level(capsys: CaptureFixture, path: str, status: int, level: str) -> None:
    _setup(level="DEBUG")
    capsys.readouterr()
    _access(path=path, status=status)
    assert _parse(capsys.readouterr().err)["level"] == level


def test_health_path_with_query_logged_at_debug(capsys: CaptureFixture) -> None:
    _setup(level="DEBUG")
    capsys.readouterr()
    _access(path="/health?probe=1")
    assert _parse(capsys.readouterr().err)["level"] == "DEBUG"


def test_query_not_logged_above_trace(capsys: CaptureFixture) -> None:
    _setup(level="DEBUG")
    capsys.readouterr()
    _access(path="/items?token=secret")
    err = capsys.readouterr().err
    assert "secret" not in err
    assert "url.path=/items" in err
    assert "url.query" not in err


def test_query_logged_at_trace(capsys: CaptureFixture) -> None:
    _setup(level="TRACE")
    capsys.readouterr()
    _access(path="/items?token=secret")
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    info, trace = (_parse(line) for line in lines)
    assert info["level"] == "INFO"
    assert info["message"] == "HTTP request."
    assert "url.query" not in info["data"]
    assert trace["level"] == "TRACE"
    assert trace["message"] == "HTTP request query string."
    assert "url.path=/items" in trace["data"]
    assert "url.query=" in trace["data"]
    assert "token=secret" in trace["data"]


def test_ipv6_client(capsys: CaptureFixture) -> None:
    _setup()
    _access(client="::1:5000")
    data = _parse(capsys.readouterr().err)["data"]
    assert "client.address=::1" in data
    assert "client.port=5000" in data


def test_client_without_port(capsys: CaptureFixture) -> None:
    _setup()
    _access(client="unix-socket")
    data = _parse(capsys.readouterr().err)["data"]
    assert "client.address=unix-socket" in data
    assert "client.port" not in data


def test_empty_client(capsys: CaptureFixture) -> None:
    _setup()
    _access(client="")
    assert "client." not in _parse(capsys.readouterr().err)["data"]


UNEXPECTED_ARGS = [
    ("%s %s", ("a", "b"), "a b", "('str', 'str')"),
    (
        ACCESS_FORMAT,
        ("a", "GET", "/", "1.1", 200.0),
        'a - "GET / HTTP/1.1" 200',
        "('str', 'str', 'str', 'str', 'float')",
    ),
]


@pytest.mark.parametrize("msg, args, expected, expected_args", UNEXPECTED_ARGS)  # type: ignore
def test_unexpected_access_record_logged_unstructured(
    capsys: CaptureFixture, msg: str, args: tuple[Any, ...], expected: str, expected_args: str
) -> None:
    _setup()
    logging.getLogger("uvicorn.access").info(msg, *args)
    warning, text = (_parse(line) for line in capsys.readouterr().err.splitlines())
    assert warning["level"] == "WARNING"
    assert warning["message"] == "Unexpected uvicorn access record, logging it unstructured."
    assert "logger_name=uvicorn.access" in warning["data"]
    assert f'args="{expected_args}"' in warning["data"]
    assert "_source=test_uvicorn_log.test_unexpected_access_record_logged_unstructured:" in warning["data"]
    assert text["level"] == "INFO"
    assert text["message"] == expected
    assert '_access_record_error="unexpected arguments"' in text["data"]
    assert "_source=test_uvicorn_log.test_unexpected_access_record_logged_unstructured:" in text["data"]


def test_unexpected_access_record_warning_logged_once(capsys: CaptureFixture) -> None:
    _setup()
    for _ in range(2):
        logging.getLogger("uvicorn.access").info("%s", "a")
    levels = [_parse(line)["level"] for line in capsys.readouterr().err.splitlines()]
    assert levels == ["WARNING", "INFO", "INFO"]


def test_unexpected_access_record_with_braces_is_not_formatted(capsys: CaptureFixture) -> None:
    _setup()
    logging.getLogger("uvicorn.access").info("GET /items/{id}")
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    assert _parse(lines[1])["message"] == "GET /items/{id}"


@pytest.mark.parametrize(
    "args",
    [
        (("127.0.0.1", 1234), "GET", "/items?token=secret", "1.1", 200),
        ("127.0.0.1:1", "GET", "/items?token=secret", "1.1", 200, "sixth"),
        (("127.0.0.1", 1234), "GET", '/items?a=sec"ret&b=1', "1.1", 200),
    ],
)  # type: ignore
def test_unexpected_access_record_does_not_leak_query(capsys: CaptureFixture, args: tuple[Any, ...]) -> None:
    _setup()
    logging.getLogger("uvicorn.access").info(ACCESS_FORMAT, *args)
    err = capsys.readouterr().err
    assert "secret" not in err
    assert "ret&b=1" not in err
    assert "Logging error" not in err


def test_preformatted_access_record_does_not_leak_query(capsys: CaptureFixture) -> None:
    _setup()
    logging.getLogger("uvicorn.access").info('127.0.0.1:5000 - "GET /items?token=secret HTTP/1.1" 200')
    err = capsys.readouterr().err
    assert "secret" not in err
    assert "Logging error" not in err


def test_access_record_exception_logged(capsys: CaptureFixture) -> None:
    _setup(data_format=DataFormat.JSON)
    try:
        raise ZeroDivisionError("zero")
    except ZeroDivisionError:
        logging.getLogger("uvicorn.access").exception("boom")
        logging.getLogger("uvicorn.access").info(ACCESS_FORMAT, "127.0.0.1:1", "GET", "/", "1.1", 500, exc_info=True)
    _, unstructured, structured = (json.loads(line)["record"] for line in capsys.readouterr().err.splitlines())
    assert unstructured["level"]["name"] == "ERROR"
    assert "ZeroDivisionError" in unstructured["extra"]["_traceback"]
    assert structured["message"] == "HTTP request."
    assert "ZeroDivisionError" in structured["extra"]["_traceback"]


class _Unprintable:
    def __str__(self) -> str:
        raise RuntimeError("lazy")


@pytest.mark.parametrize(
    "msg, args, error",
    [
        ("%d", ("x",), "TypeError"),
        ("100%", ("x",), "ValueError"),
        ("%(name)s", ({"other": 1},), "KeyError"),
        ("%s", (_Unprintable(),), "RuntimeError"),
    ],
)  # type: ignore
def test_unformattable_access_record_logged_as_warning(
    capsys: CaptureFixture, msg: str, args: tuple[Any, ...], error: str
) -> None:
    _setup()
    logging.getLogger("uvicorn.access").info(msg, *args)
    err = capsys.readouterr().err
    lines = err.splitlines()
    assert len(lines) == 2
    assert all(_parse(line)["level"] == "WARNING" for line in lines)
    parsed = _parse(lines[-1])
    assert parsed["message"] == "Could not format log record."
    assert "_source=test_uvicorn_log.test_unformattable_access_record_logged_as_warning:" in parsed["data"]
    assert "msg=" in parsed["data"]
    assert "args=" in parsed["data"]
    assert re.search(rf"error=\"?{error}\(", parsed["data"])
    assert "Logging error" not in err


def test_unformattable_access_record_does_not_log_arg_values(capsys: CaptureFixture) -> None:
    _setup()
    logging.getLogger("uvicorn.access").info(
        ACCESS_FORMAT, "127.0.0.1:1", "GET", "/items?token=secret", "1.1", 200, "x"
    )
    err = capsys.readouterr().err
    assert "secret" not in err
    assert "127.0.0.1" not in err
    assert "args=" in err


def test_unformattable_access_record_does_not_log_query_in_msg(capsys: CaptureFixture) -> None:
    _setup()
    logging.getLogger("uvicorn.access").info("GET /items?token=secret %d", "x")
    err = capsys.readouterr().err
    assert "Could not format log record." in err
    assert "secret" not in err


def test_unformattable_access_record_with_non_tuple_args(capsys: CaptureFixture) -> None:
    _setup()
    logging.getLogger("uvicorn.access").info("%(a)d", {"a": "x"})
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    assert all(_parse(line)["level"] == "WARNING" for line in lines)
    parsed = _parse(lines[-1])
    assert parsed["message"] == "Could not format log record."
    assert "args=dict" in parsed["data"]


def test_uvicorn_logger_propagates_to_lancelog(capsys: CaptureFixture) -> None:
    _setup()
    logging.getLogger("uvicorn.error").info("Started server process [1]")
    assert _parse(capsys.readouterr().err)["message"] == "Started server process [1]"


def test_uvicorn_log_config_does_not_touch_root() -> None:
    _setup()
    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    assert isinstance(handlers[0], _InterceptHandler)


def test_custom_level_name_does_not_raise(capsys: CaptureFixture) -> None:
    logging.addLevelName(25, "INFOWARN")
    _setup()
    logging.getLogger("uvicorn.access").log(25, ACCESS_FORMAT, "127.0.0.1:5000", "GET", "/", "1.1", 200)
    assert _parse(capsys.readouterr().err)["level"] == "INFOWARN"


def test_client_with_non_ascii_digit_port(capsys: CaptureFixture) -> None:
    _setup()
    _access(client="host:\u00b2")
    data = _parse(capsys.readouterr().err)["data"]
    assert "client.address=host:\u00b2" in data
    assert "client.port" not in data


def test_import_does_not_import_uvicorn() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import sys, lancelog; print('uvicorn' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


def test_uvicorn_is_not_a_runtime_dependency() -> None:
    uvicorn_requirements = [req for req in requires("lancelog") or [] if "uvicorn" in req]
    assert all('extra == "test"' in req for req in uvicorn_requirements)


@pytest.mark.parametrize("health_paths", ["/health", None])  # type: ignore
def test_health_paths_rejects_str_and_none(health_paths: Any) -> None:
    with pytest.raises(TypeError, match="health_paths must be an iterable of paths, not a str or None"):
        uvicorn_log_config(health_paths=health_paths)


@pytest.mark.parametrize("handlers", [[], [logging.StreamHandler()]])  # type: ignore
def test_uvicorn_log_config_requires_setup_logging(handlers: list[logging.Handler]) -> None:
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers[:] = handlers
    try:
        with pytest.raises(RuntimeError, match=re.escape("Call lancelog.setup_logging() before uvicorn_log_config()")):
            uvicorn_log_config()
    finally:
        root.handlers[:] = saved


@pytest.mark.parametrize("value", ["host", None])  # type: ignore
def test_log_request_headers_rejects_str_and_none(value: Any) -> None:
    with pytest.raises(
        TypeError, match="log_request_headers must be a bool or an iterable of header names, not a str or None"
    ):
        uvicorn_log_config(log_request_headers=value)


def test_log_request_headers_names_are_normalized() -> None:
    setup_logging(sink=sys.stderr)
    config = uvicorn_log_config(log_request_headers=("Host", "X-Request-ID"))
    assert config["handlers"]["lancelog_access"]["log_request_headers"] == frozenset({"host", "x-request-id"})
    assert uvicorn_log_config(log_request_headers=True)["handlers"]["lancelog_access"]["log_request_headers"] is True


def test_log_request_headers_defaults_to_false() -> None:
    setup_logging(sink=sys.stderr)
    assert uvicorn_log_config()["handlers"]["lancelog_access"]["log_request_headers"] is False


def test_request_headers_and_query_logged_at_trace(capsys: CaptureFixture) -> None:
    _setup(level="TRACE", log_request_headers=True)
    capsys.readouterr()
    _RequestResponseCycle(HEADERS).send("/items?token=secret")
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    info, trace = (_parse(line) for line in lines)
    assert "http.request.header" not in info["data"]
    assert trace["level"] == "TRACE"
    assert trace["message"] == "HTTP request headers and query string."
    for token in (
        "url.path=/items",
        "url.query=",
        "token=secret",
        'http.request.headers="{',
        "_source=test_uvicorn_log.send:",
    ):
        assert token in trace["data"]
    assert "http.request.header." not in trace["data"]


def test_selected_request_headers_logged_at_trace(capsys: CaptureFixture) -> None:
    _setup(level="TRACE", log_request_headers=["Host", "x-multi", "x-absent"])
    capsys.readouterr()
    _RequestResponseCycle(HEADERS).send("/items?token=secret")
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    trace = _parse(lines[1])
    assert trace["message"] == "HTTP request headers and query string."
    for token in (
        "http.request.header.host=example.org",
        'http.request.header.x-multi="a, b"',
        "url.query=",
        "token=secret",
        "_source=test_uvicorn_log.send:",
    ):
        assert token in trace["data"]
    for token in ("http.request.header.accept", "x-absent", "http.request.headers=", "_request_headers_error"):
        assert token not in trace["data"]


def test_selected_request_headers_json(capsys: CaptureFixture) -> None:
    _setup(level="TRACE", data_format=DataFormat.JSON, log_request_headers=["x-multi"])
    capsys.readouterr()
    _RequestResponseCycle(HEADERS).send("/items?a=1")
    extra = json.loads(capsys.readouterr().err.splitlines()[1])["record"]["extra"]
    assert extra["http.request.header.x-multi"] == "a, b"
    assert "http.request.headers" not in extra


def test_empty_request_header_list_logs_nothing(capsys: CaptureFixture) -> None:
    _setup(level="TRACE", log_request_headers=[])
    capsys.readouterr()
    _RequestResponseCycle(HEADERS).send("/items?token=secret")
    trace = _parse(capsys.readouterr().err.splitlines()[1])
    assert trace["message"] == "HTTP request query string."
    assert "http.request.header" not in trace["data"]


def test_emit_failure_does_not_propagate(capsys: CaptureFixture) -> None:
    _setup(level="TRACE", log_request_headers=True)
    capsys.readouterr()
    with patch("lancelog.uvicorn_log._request_header_fields", side_effect=RuntimeError("boom")):
        _RequestResponseCycle(HEADERS).send()
    err = capsys.readouterr().err
    assert _parse(err.splitlines()[0])["message"] == "HTTP request."
    assert "--- Logging error ---" in err
    assert "RuntimeError: boom" in err
    assert "HTTP request headers and query string." not in err


def test_request_headers_logged_without_query(capsys: CaptureFixture) -> None:
    _setup(level="TRACE", log_request_headers=True)
    capsys.readouterr()
    _RequestResponseCycle(HEADERS).send("/items")
    trace = _parse(capsys.readouterr().err.splitlines()[1])
    assert trace["message"] == "HTTP request headers and query string."
    assert 'http.request.headers="{' in trace["data"]
    assert "url.query" not in trace["data"]


def test_request_headers_json(capsys: CaptureFixture) -> None:
    _setup(level="TRACE", data_format=DataFormat.JSON, log_request_headers=True)
    capsys.readouterr()
    _RequestResponseCycle(HEADERS).send("/items?a=1")
    extra = json.loads(capsys.readouterr().err.splitlines()[1])["record"]["extra"]
    assert extra["url.query"] == "a=1"
    assert json.loads(extra["http.request.headers"]) == {"host": "example.org", "accept": "*/*", "x-multi": "a, b"}
    assert "http.request.header.host" not in extra
    assert "_request_headers_error" not in extra


def test_request_headers_logfmt_value_is_json(capsys: CaptureFixture) -> None:
    _setup(level="TRACE", log_request_headers=True)
    capsys.readouterr()
    tricky = b'back\\slash "quoted"'
    _RequestResponseCycle([*HEADERS, (b"x-tricky", tricky)]).send()
    data = _parse(capsys.readouterr().err.splitlines()[1])["data"]
    match = re.search(r'http\.request\.headers="((?:[^"\\]|\\.)*)"', data)
    assert match
    expected = {"host": "example.org", "accept": "*/*", "x-multi": "a, b", "x-tricky": tricky.decode()}
    assert json.loads(re.sub(r"\\(.)", r"\1", match.group(1))) == expected


def test_request_headers_not_logged_above_trace(capsys: CaptureFixture) -> None:
    _setup(level="DEBUG", log_request_headers=True)
    capsys.readouterr()
    _RequestResponseCycle(HEADERS).send("/items?token=secret")
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    assert "example.org" not in err
    assert "secret" not in err


def test_request_headers_not_logged_by_default(capsys: CaptureFixture) -> None:
    _setup(level="TRACE")
    capsys.readouterr()
    _RequestResponseCycle(HEADERS).send("/items?token=secret")
    trace = _parse(capsys.readouterr().err.splitlines()[1])
    assert trace["message"] == "HTTP request query string."
    assert "http.request.header" not in trace["data"]


def test_request_headers_scope_missing(capsys: CaptureFixture) -> None:
    _setup(level="TRACE", log_request_headers=True)
    capsys.readouterr()
    _access()
    trace = _parse(capsys.readouterr().err.splitlines()[1])
    assert trace["message"] == "HTTP request headers and query string."
    assert '_request_headers_error="request scope not found"' in trace["data"]
    assert "http.request.headers" not in trace["data"]
    assert "url.path=/items" in trace["data"]


@pytest.mark.parametrize("names", [True, ["x-test"]])  # type: ignore
def test_request_headers_bad_entry_is_marked_and_rest_kept(capsys: CaptureFixture, names: bool | list[str]) -> None:
    _setup(level="TRACE", data_format=DataFormat.JSON, log_request_headers=names)
    capsys.readouterr()
    _RequestResponseCycle([("host", "example.org"), (b"x-test", b"abc"), (b"bad",)]).send()
    extra = json.loads(capsys.readouterr().err.splitlines()[1])["record"]["extra"]
    assert extra["_request_headers_error"].startswith("AttributeError(")
    if names is True:
        assert json.loads(extra["http.request.headers"]) == {"x-test": "abc"}
    else:
        assert extra["http.request.header.x-test"] == "abc"


def test_request_headers_scope_without_headers(capsys: CaptureFixture) -> None:
    _setup(level="TRACE", data_format=DataFormat.JSON, log_request_headers=True)
    capsys.readouterr()
    cycle = _RequestResponseCycle([])
    del cycle.scope["headers"]
    cycle.send()
    extra = json.loads(capsys.readouterr().err.splitlines()[1])["record"]["extra"]
    assert extra["http.request.headers"] == "{}"


def test_trace_enabled_follows_loguru_minimum_level() -> None:
    setup_logging(sink=sys.stderr, level="INFO")
    assert _trace_enabled() is False
    setup_logging(sink=sys.stderr, level="TRACE")
    assert _trace_enabled() is True
    logger.remove()
    assert _trace_enabled() is False
    setup_logging(sink=sys.stderr, level="INFO")
    logger.add(io.StringIO(), level="TRACE")
    assert _trace_enabled() is True
    setup_logging(sink=sys.stderr, level="INFO")


@pytest.mark.parametrize("names", [True, ["host"]])  # type: ignore
def test_request_headers_not_extracted_above_trace(capsys: CaptureFixture, names: bool | list[str]) -> None:
    _setup(level="DEBUG", log_request_headers=names)
    capsys.readouterr()
    with patch("lancelog.uvicorn_log._request_header_fields", wraps=_request_header_fields) as spy:
        _RequestResponseCycle(HEADERS).send("/items?token=secret")
    err = capsys.readouterr().err
    assert spy.call_count == 0
    assert len(err.splitlines()) == 1
    assert "example.org" not in err
    assert "secret" not in err


def test_request_headers_extracted_once_at_trace() -> None:
    _setup(level="TRACE", log_request_headers=True)
    with patch("lancelog.uvicorn_log._request_header_fields", wraps=_request_header_fields) as spy:
        _RequestResponseCycle(HEADERS).send("/items?token=secret")
    assert spy.call_count == 1


def test_existing_loggers_are_not_disabled(capsys: CaptureFixture) -> None:
    app_logger = logging.getLogger("my_app.service")
    _setup()
    app_logger.info("still here")
    assert _parse(capsys.readouterr().err)["message"] == "still here"


def test_uvicorn_log_config_logs_configuration(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr, level="DEBUG")
    capsys.readouterr()
    config = uvicorn_log_config(health_paths=iter(["/ready", "/health"]), log_request_headers=True)
    parsed = _parse(capsys.readouterr().err)
    assert parsed["level"] == "DEBUG"
    assert parsed["message"] == "Configured uvicorn logging."
    assert "health_paths=\"['/health', '/ready']\"" in parsed["data"]
    assert "log_request_headers=true" in parsed["data"]
    assert config["handlers"]["lancelog_access"]["health_paths"] == frozenset({"/health", "/ready"})


def test_uvicorn_log_config_logs_normalized_header_names(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr, level="DEBUG")
    capsys.readouterr()
    uvicorn_log_config(log_request_headers=iter(["X-Test", "Authorization"]))
    data = _parse(capsys.readouterr().err)["data"]
    assert "health_paths=[]" in data
    assert "log_request_headers=\"['authorization', 'x-test']\"" in data


@pytest.mark.parametrize(
    "logger_name, msg, args, levels",
    [
        ("my_app", "boom", (), ["ERROR"]),
        ("uvicorn.access", ACCESS_FORMAT, ("127.0.0.1:1", "GET", "/", "1.1", 500), ["ERROR"]),
        ("uvicorn.access", "%s %s", ("a", "b"), ["WARNING", "ERROR"]),
    ],
)  # type: ignore
def test_exc_info_true_outside_except_stays_one_line(
    capsys: CaptureFixture, logger_name: str, msg: str, args: tuple[Any, ...], levels: list[str]
) -> None:
    _setup()
    capsys.readouterr()
    logging.getLogger(logger_name).error(msg, *args, exc_info=True)
    err = capsys.readouterr().err
    assert "NoneType" not in err
    assert [_parse(line)["level"] for line in err.splitlines()] == levels
