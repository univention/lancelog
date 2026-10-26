# SPDX-License-Identifier: AGPL-3.0-only

"""Integrate uvicorn's logging with Lancelog."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from contextvars import ContextVar
from types import FrameType
from typing import Any, Literal, TypeGuard

from loguru import logger

from .setup import _InterceptHandler, _exception_info, _format_message, _loguru_level, _stdlib_caller_depth


_ACCESS_LOG_MESSAGE = "HTTP request."
_ACCESS_LOG_QUERY_MESSAGE = "HTTP request query string."
_ACCESS_LOG_HEADERS_MESSAGE = "HTTP request headers and query string."
_UNSTRUCTURED_ACCESS_MESSAGE = "Unexpected uvicorn access record, logging it unstructured."
_QUERY_STRING = re.compile(r"\?\S*")
_HEADERS_FIELD = "http.request.headers"
_HEADER_FIELD_PREFIX = "http.request.header."
_HEADERS_ERROR_FIELD = "_request_headers_error"
_NO_SCOPE = "request scope not found"
_TRACE_NO = logger.level("TRACE").no
_DURATION_FIELD = "http.server.request.duration"

# ASGI types, so Lancelog needs neither Starlette nor uvicorn.
_Scope = MutableMapping[str, Any]
_Message = MutableMapping[str, Any]
_Receive = Callable[[], Awaitable[_Message]]
_Send = Callable[[_Message], Awaitable[None]]
_ASGIApp = Callable[[_Scope, _Receive, _Send], Awaitable[None]]

_request_start: ContextVar[float] = ContextVar("lancelog_request_start")


class RequestTimingMiddleware:
    """
    Pure ASGI middleware that records when a request arrives,
    so the uvicorn access record can carry ``http.server.request.duration``.

    It must be the outermost middleware, so it also times the other middlewares.
    In FastAPI and Starlette, add it last: ``app.add_middleware(RequestTimingMiddleware)``.
    In ``FastAPI(middleware=[...])`` or ``Starlette(middleware=[...])``, the list is outermost-first,
    so put it first.
    A ``BaseHTTPMiddleware`` or ``@app.middleware("http")`` outside of it hides the field,
    because that middleware sends the response from another task.
    Only HTTP requests are timed, other scope types (``websocket``, ``lifespan``) pass through.
    """

    def __init__(self, app: _ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope["type"] == "http":
            # No need to reset the ContextVar in a finally block. Every request runs in its own context.
            _request_start.set(time.perf_counter())
        await self.app(scope, receive, send)


def _is_access_args(args: Any) -> TypeGuard[tuple[str, str, str, str, int]]:
    return (
        isinstance(args, tuple)
        and len(args) == 5
        and all(isinstance(arg, str) for arg in args[:4])
        and isinstance(args[4], int)
    )


def _arg_types(args: Any) -> str:
    """Describe the shape of ``LogRecord.args`` without its values, which hold request data."""
    return repr(tuple(type(arg).__name__ for arg in args)) if isinstance(args, tuple) else type(args).__name__


def _redacted_repr(value: Any) -> str:
    """``repr()`` without query strings: a pre-formatted ``LogRecord.msg`` carries the request line."""
    return _QUERY_STRING.sub("", repr(value))


def _trace_enabled() -> bool:
    """Mirror the early return of Loguru's ``Logger._log()``; ``_core.min_level`` is private API."""
    return bool(logger._core.min_level <= _TRACE_NO)


def _request_header_fields(frame: FrameType, names: Literal[True] | frozenset[str]) -> dict[str, str]:
    """Read the request headers from the ``scope`` of the uvicorn ``RequestResponseCycle`` that logged the record."""
    scope = getattr(frame.f_locals.get("self"), "scope", None)
    if not isinstance(scope, Mapping):
        return {_HEADERS_ERROR_FIELD: _NO_SCOPE}
    headers: dict[str, list[str]] = {}
    fields: dict[str, str] = {}
    for entry in scope.get("headers", ()):
        try:
            name, value = entry
            key = name.decode("latin-1").lower()
            text = value.decode("latin-1")
        except (AttributeError, TypeError, ValueError) as exc:
            fields.setdefault(_HEADERS_ERROR_FIELD, repr(exc))
            continue
        if names is True or key in names:
            headers.setdefault(key, []).append(text)
    joined = {key: ", ".join(values) for key, values in headers.items()}
    if names is True:
        fields[_HEADERS_FIELD] = json.dumps(joined)
    else:
        fields.update((_HEADER_FIELD_PREFIX + key, value) for key, value in joined.items())
    return fields


def _client_fields(client_addr: str) -> dict[str, Any]:
    if not client_addr:
        return {}
    address, _, port = client_addr.rpartition(":")
    if port.isdecimal():
        return {"client.address": address, "client.port": int(port)}
    return {"client.address": client_addr}


class _UvicornAccessHandler(logging.Handler):
    """Turn uvicorn access log records into structured Loguru records."""

    def __init__(
        self, health_paths: frozenset[str] = frozenset(), log_request_headers: bool | frozenset[str] = False
    ) -> None:
        super().__init__()
        self.health_paths = health_paths
        self.log_request_headers = log_request_headers
        self._warned_unstructured = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            depth = _stdlib_caller_depth()
            if not _is_access_args(record.args):
                self._emit_unstructured(record, depth + 1)
                return
            now = time.perf_counter()
            client_addr, method, path_with_query, http_version, status_code = record.args
            path, _, query = path_with_query.partition("?")
            fields: dict[str, Any] = _client_fields(client_addr)
            fields["http.request.method"] = method
            fields["url.path"] = path
            fields["network.protocol.version"] = http_version
            fields["http.response.status_code"] = status_code
            start = _request_start.get(None)
            if start is not None:
                fields[_DURATION_FIELD] = round(now - start, 4)
            level = self._access_level(record, path, status_code)
            logger.opt(depth=depth, exception=_exception_info(record)).log(level, _ACCESS_LOG_MESSAGE, **fields)
            if _trace_enabled():
                self._emit_trace(depth + 1, path, query)
        except Exception:
            self.handleError(record)

    def _emit_unstructured(self, record: logging.LogRecord, depth: int) -> None:
        # If uvicorn changes the access record format/data model, a warning is logged once per process.
        # The records are then logged as text with the field ``_access_record_error="unexpected arguments"``.
        if not self._warned_unstructured:
            self._warned_unstructured = True
            logger.opt(depth=depth).warning(
                _UNSTRUCTURED_ACCESS_MESSAGE,
                logger_name=record.name,
                msg=_redacted_repr(record.msg),
                args=_arg_types(record.args),
            )
        message = _format_message(record, depth, _arg_types, _redacted_repr)
        if message is not None:
            logger.bind(_access_record_error="unexpected arguments").opt(
                depth=depth, exception=_exception_info(record)
            ).log(_loguru_level(record), _QUERY_STRING.sub("", message))

    def _access_level(self, record: logging.LogRecord, path: str, status_code: int) -> str | int:
        if status_code >= 500:
            return "ERROR"
        if status_code < 300 and path in self.health_paths:
            return "DEBUG"
        return _loguru_level(record)

    def _emit_trace(self, depth: int, path: str, query: str) -> None:
        trace_fields: dict[str, Any] = {"url.path": path}
        if query:
            trace_fields["url.query"] = query
        if self.log_request_headers:
            trace_fields.update(_request_header_fields(sys._getframe(depth), self.log_request_headers))
            logger.opt(depth=depth).trace(_ACCESS_LOG_HEADERS_MESSAGE, **trace_fields)
        elif query:
            logger.opt(depth=depth).trace(_ACCESS_LOG_QUERY_MESSAGE, **trace_fields)


def uvicorn_log_config(
    health_paths: Iterable[str] = (),
    log_request_headers: bool | Iterable[str] = False,
) -> dict[str, Any]:
    """
    Return a ``logging.config.dictConfig`` dict for ``uvicorn.run(app, log_config=...)``.

    Call ``setup_logging()`` first. The root logger is not touched.
    Records of the ``uvicorn`` loggers propagate to the root logger and thus to Loguru.

    The ``uvicorn`` loggers are fixed at ``INFO``, whatever level ``setup_logging()`` uses.
    Below ``INFO`` uvicorn logs per-connection protocol chatter
    (``HTTP connection made``, every ASGI message at ``TRACE``),
    which a ``setup_logging(level="DEBUG")`` meant for the application should log.
    To lower uvicorn's log level, pass ``log_level="debug"`` or ``log_level="trace"`` to ``uvicorn.run()``,
    which uvicorn applies after this config, and use that level in ``setup_logging()`` too.

    Access log records (``uvicorn.access``) become structured records
    with the message ``HTTP request.`` and these fields:

    - ``client.address``: ``str``, omitted if uvicorn reports no client
    - ``client.port``: ``int``, omitted if the client address has no port
    - ``http.request.method``: ``str``
    - ``url.path``: ``str``, without the query string
    - ``network.protocol.version``: ``str``, e.g. ``1.1``
    - ``http.response.status_code``: ``int``
    - ``http.server.request.duration``: ``float``, seconds,
      from when ``RequestTimingMiddleware`` received the request
      until the response status and headers are sent
      (the body transfer of streaming responses is not included).
      Only present when ``RequestTimingMiddleware`` is used.

    The query string and the request headers may contain secrets and are never part of that record.
    The query string is logged separately at level ``TRACE`` as ``HTTP request query string.``
    with the fields ``url.path`` and ``url.query``.
    With ``log_request_headers``, that ``TRACE`` record is written for every request,
    has the message ``HTTP request headers and query string.`` and the request headers:

    - ``True``: all headers in one field ``http.request.headers``,
      a JSON object as a string, e.g. ``{"host": "example.org", "accept": "*/*"}``.
    - an iterable of header names (case-insensitive): one field ``http.request.header.<name>``
      per listed header the request carries, e.g. ``http.request.header.user-agent``.
      Each distinct field name costs a mapping entry in log stores such as OpenSearch,
      which is why ``True`` uses a single field.

    Header names are lowercase, values of a repeated header are joined with ``, ``.
    uvicorn does not pass the headers to the access log record,
    so they are read from the request cycle that wrote it.
    If that fails, e.g. after a uvicorn update, the record carries the field ``_request_headers_error``
    with the reason, and the headers that could still be read.
    Those ``TRACE`` records reach the sink only if Lancelog runs at ``TRACE`` (``setup_logging(level="TRACE")``).

    Access records are logged at ``INFO``,
    at ``ERROR`` if the status code is 500 or above (every path),
    and at ``DEBUG`` if the status code is below 300 and the path is in ``health_paths``.

    uvicorn's ``use_colors`` option has no effect, colors come from ``setup_logging(colorize=...)``.

    :param health_paths: Request paths (without query string) of health check probes,
        whose successful requests (status below 300) are logged at ``DEBUG``.
        They are compared with the path as uvicorn logs it:
        percent-encoded and, on newer uvicorn versions, including ``root_path`` (e.g. ``/api/health``).
    :param log_request_headers: ``False`` (default): do not log request headers.
        ``True``: log all of them in one field.
        An iterable of header names: log those, one field each.
        All at ``TRACE``, see above.
    :raises RuntimeError: If ``setup_logging()`` has not been called.
    :raises TypeError: If ``health_paths`` or ``log_request_headers`` is a ``str`` or ``None``.
    """
    if health_paths is None or isinstance(health_paths, str):
        raise TypeError("health_paths must be an iterable of paths, not a str or None")
    if log_request_headers is None or isinstance(log_request_headers, str):
        raise TypeError("log_request_headers must be a bool or an iterable of header names, not a str or None")
    if not any(isinstance(handler, _InterceptHandler) for handler in logging.getLogger().handlers):
        raise RuntimeError(
            "The root logger has no Lancelog handler. Call lancelog.setup_logging() before uvicorn_log_config()."
        )
    paths = frozenset(health_paths)
    headers = (
        log_request_headers
        if isinstance(log_request_headers, bool)
        else frozenset(n.lower() for n in log_request_headers)
    )
    logger.debug(
        "Configured uvicorn logging.",
        health_paths=sorted(paths),
        log_request_headers=headers if isinstance(headers, bool) else sorted(headers),
    )
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {}, "access": {}},
        "handlers": {
            "lancelog_access": {
                "()": _UvicornAccessHandler,
                "health_paths": paths,
                "log_request_headers": headers,
            }
        },
        "loggers": {
            "uvicorn": {"handlers": [], "level": "INFO", "propagate": True},
            "uvicorn.access": {"handlers": ["lancelog_access"], "level": "INFO", "propagate": False},
        },
    }
