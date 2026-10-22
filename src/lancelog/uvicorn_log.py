# SPDX-License-Identifier: AGPL-3.0-only

"""Integrate uvicorn's logging with Lancelog."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any, TypeGuard

from loguru import logger

from .setup import _InterceptHandler, _exception_info, _format_message, _loguru_level, _stdlib_caller_depth


_ACCESS_LOG_MESSAGE = "HTTP request."
_ACCESS_LOG_QUERY_MESSAGE = "HTTP request query string."
_UNSTRUCTURED_ACCESS_MESSAGE = "Unexpected uvicorn access record, logging it unstructured."
_QUERY_STRING = re.compile(r"\?\S*")


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


class _UvicornAccessHandler(logging.Handler):
    """Turn uvicorn access log records into structured Lancelog records."""

    def __init__(self, health_paths: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self.health_paths = health_paths
        self._warned_unstructured = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            depth = _stdlib_caller_depth()
            level = _loguru_level(record)
            if not _is_access_args(record.args):
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
                    ).log(level, _QUERY_STRING.sub("", message))
                return

            client_addr, method, path_with_query, http_version, status_code = record.args
            path, _, query = path_with_query.partition("?")
            fields: dict[str, Any] = {}
            if client_addr:
                address, _, port = client_addr.rpartition(":")
                if port.isdecimal():
                    fields["client.address"] = address
                    fields["client.port"] = int(port)
                else:
                    fields["client.address"] = client_addr
            fields["http.request.method"] = method
            fields["url.path"] = path
            fields["network.protocol.version"] = http_version
            fields["http.response.status_code"] = status_code

            if status_code >= 500:
                level = "ERROR"
            elif status_code < 300 and path in self.health_paths:
                level = "DEBUG"
            logger.opt(depth=depth, exception=_exception_info(record)).log(level, _ACCESS_LOG_MESSAGE, **fields)
            if query:
                logger.opt(depth=depth).trace(_ACCESS_LOG_QUERY_MESSAGE, **{"url.path": path, "url.query": query})
        except Exception:
            self.handleError(record)


def uvicorn_log_config(health_paths: Iterable[str] = ()) -> dict[str, Any]:
    """
    Return a ``logging.config.dictConfig`` dict for ``uvicorn.run(app, log_config=...)``.

    Call ``setup_logging()`` first. The root logger is not touched.
    Records of the ``uvicorn`` loggers propagate to the root logger and thus to Lancelog.

    The ``uvicorn`` loggers are fixed at ``INFO``, whatever level ``setup_logging()`` uses.
    Below ``INFO`` uvicorn logs per-connection protocol chatter
    (``HTTP connection made``, every ASGI message at ``TRACE``),
    which a ``setup_logging(level="DEBUG")`` meant for the application should not pull in.
    To get it, pass ``log_level="debug"`` or ``log_level="trace"`` to ``uvicorn.run()``,
    which uvicorn applies after this config, and use that level in ``setup_logging()`` too.

    Access log records (``uvicorn.access``) become structured records
    with the message ``HTTP request.`` and these fields:

    - ``client.address``: ``str``, omitted if uvicorn reports no client
    - ``client.port``: ``int``, omitted if the client address has no port
    - ``http.request.method``: ``str``
    - ``url.path``: ``str``, without the query string
    - ``network.protocol.version``: ``str``, e.g. ``1.1``
    - ``http.response.status_code``: ``int``

    The query string may contain secrets and is never part of that record.
    It is logged separately at level ``TRACE`` as ``HTTP request query string.``
    with the fields ``url.path`` and ``url.query``.
    That ``TRACE`` record reaches the sink only if Lancelog runs at ``TRACE`` (``setup_logging(level="TRACE")``).

    If uvicorn changes the access record,
    a ``WARNING`` ``Unexpected uvicorn access record, logging it unstructured.`` is logged once per process
    with the fields ``logger_name``, ``msg`` (without query strings) and ``args`` (the argument types, not the values).
    The records are then logged as text at their own level
    with the field ``_access_record_error="unexpected arguments"`` and without the query string.
    A record whose message cannot be formatted is logged as ``WARNING`` ``Could not format log record.``
    with the fields ``msg`` (without query strings), ``args`` (the argument types) and ``error``.
    A failure while logging a record is reported through ``handleError()`` and does not fail the request.

    Access records are logged at ``INFO``,
    at ``ERROR`` if the status code is 500 or above (every path),
    and at ``DEBUG`` if the status code is below 300 and the path is in ``health_paths``.

    uvicorn's ``use_colors`` option has no effect, colors come from ``setup_logging(colorize=...)``.

    :param health_paths: Request paths (without query string) of health check probes,
        whose successful requests (status below 300) are logged at ``DEBUG``.
        They are compared with the path as uvicorn logs it:
        percent-encoded and, on newer uvicorn versions, including ``root_path`` (e.g. ``/api/health``).
    :raises RuntimeError: If ``setup_logging()`` has not been called.
    :raises TypeError: If ``health_paths`` is a ``str`` or ``None``.
    """
    if health_paths is None or isinstance(health_paths, str):
        raise TypeError("health_paths must be an iterable of paths, not a str or None")
    if not any(isinstance(handler, _InterceptHandler) for handler in logging.getLogger().handlers):
        raise RuntimeError(
            "The root logger has no Lancelog handler. Call lancelog.setup_logging() before uvicorn_log_config()."
        )
    paths = frozenset(health_paths)
    logger.debug("Configured uvicorn logging.", health_paths=sorted(paths))
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {}, "access": {}},
        "handlers": {"lancelog_access": {"()": _UvicornAccessHandler, "health_paths": paths}},
        "loggers": {
            "uvicorn": {"handlers": [], "level": "INFO", "propagate": True},
            "uvicorn.access": {"handlers": ["lancelog_access"], "level": "INFO", "propagate": False},
        },
    }
